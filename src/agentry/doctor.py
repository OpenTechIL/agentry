"""``agy doctor`` — a read-only preflight that turns silent failure modes into loud, explicit
checks before they bite at install or runtime.

The design principle (from the apm pain-points analysis): *agentry tells you, loudly, rather
than guessing.* This consolidates the warnings already scattered across resolve/sync and adds
the genuine gaps — most notably an **unset-`${VAR}` scan** over MCP/hook fragments (a dead
placeholder ships silently otherwise). Hard problems (undefined target, unknown source, a
component its source doesn't provide) are **errors** (non-zero exit); softer ones (a type no
active target installs, an unset env var your agent resolves at runtime, on-disk drift) are
**warnings**. ``run_checks`` is the engine; the CLI renders and sets the exit code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import deps, discovery
from .config import ConfigStore
from .drivers import resolve_drivers
from .envscan import unset_env_refs
from .lockfile import load_lock
from .models import MERGE_TYPES, ComponentType
from .reconcile import status
from .resolver import ResolveError, effective_root
from .targets import unresolved_targets


@dataclass(frozen=True)
class Check:
    level: str  # "error" | "warn" | "ok"
    category: str
    message: str


def run_checks(root: Path) -> list[Check]:
    """Run all preflight checks for the project at ``root``. Read-only."""
    config = ConfigStore.load(root).parsed()
    checks: list[Check] = []

    for t in unresolved_targets(config):
        checks.append(
            Check(
                "error", "target", f"target '{t}' is undefined — no built-in, profile, or overlay"
            )
        )

    try:
        graph, _ = deps.resolve_graph(root, config, load_lock(root))
    except (ResolveError, deps.DependencyError) as exc:
        checks.append(Check("error", "resolve", str(exc)))
        return checks

    augmented = config.model_copy(update={"sources": graph.sources, "components": graph.components})
    drivers = resolve_drivers(config)
    sources_by_name = {s.name: s for s in augmented.sources}
    indexes: dict[str, dict[tuple[ComponentType, str], Path]] = {}
    for src in augmented.sources:
        sp = effective_root(root, src)
        if sp.exists():
            indexes[src.name] = discovery.index(sp)

    for comp in augmented.components:
        if not comp.enabled:
            continue
        src = sources_by_name.get(comp.source)
        if src is None:
            checks.append(
                Check("error", "component", f"{comp.ref}: unknown source '{comp.source}'")
            )
            continue
        if comp.generate is not None:
            continue  # self-installing; artifact resolution doesn't apply
        if comp.path is not None:
            artifact = effective_root(root, src) / comp.path
        else:
            artifact = indexes.get(comp.source, {}).get((comp.type, comp.name))
        if artifact is None or not artifact.exists():
            checks.append(
                Check("error", "component", f"{comp.ref}: not provided by source '{comp.source}'")
            )
            continue
        installs_into = [
            t
            for t in comp.applies_to(config.targets)
            if (d := drivers.get(t)) and d.supports(comp.type)
        ]
        if not installs_into:
            checks.append(
                Check(
                    "warn",
                    "support",
                    f"{comp.ref}: no active target installs a '{comp.type.value}'",
                )
            )
        if comp.type in MERGE_TYPES and artifact.is_file():
            for var in unset_env_refs(artifact.read_text(encoding="utf-8")):
                checks.append(
                    Check(
                        "warn",
                        "env",
                        f"{comp.ref}: references ${{{var}}}, which is unset — set it before your "
                        "agent runs (agentry ships the placeholder; the runtime resolves it)",
                    )
                )

    try:
        rows, _ = status(root)
        for r in rows:
            if r.state != "ok":
                checks.append(
                    Check("warn", "drift", f"{r.ref} → {r.target}: {r.state} (run `agy sync`)")
                )
    except (ResolveError, deps.DependencyError):
        pass  # resolution errors already reported above

    if not checks:
        checks.append(Check("ok", "all", "all targets resolve, every component installs, no drift"))
    return checks
