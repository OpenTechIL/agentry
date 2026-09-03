"""``agentry doctor`` — a read-only preflight that turns silent failure modes into loud, explicit
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

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import deps, discovery
from .config import ConfigStore
from .drivers import Driver, resolve_drivers
from .envscan import unset_env_refs
from .lockfile import load_lock
from .models import MERGE_TYPES, Component, ComponentType, Config, Source
from .progname import CANONICAL, prog
from .reconcile import status
from .resolver import ResolveError, effective_root
from .targets import unresolved_targets


@dataclass(frozen=True)
class Check:
    level: str  # "error" | "warn" | "ok"
    category: str
    message: str


def command_name_check() -> Check | None:
    """Warn when the `agy` alias on PATH belongs to a different tool.

    ``agy`` is also Google's Antigravity CLI. When both are installed, PATH order decides
    silently which one runs — a confusing failure to debug from the symptom alone, so
    doctor names it explicitly.

    Ownership is decided by **directory**, not by comparing files: agentry's three names are
    separate console-script wrappers (or shims, or symlinks) that live side by side in one
    bin dir, so a file-identity check would flag every ordinary install as a conflict.
    """
    found = shutil.which("agy")
    if not found:
        return None
    try:
        mine = Path(sys.argv[0]).resolve().parent
        theirs = Path(found).resolve().parent
    except OSError:
        return None
    if mine == theirs:
        return None  # installed alongside us — our own alias
    return Check(
        "warn",
        "command",
        f"`agy` on PATH is {found}, not this agentry (Google's Antigravity CLI also uses "
        f"the name) — invoke agentry as `{CANONICAL}` or `agyx` to be unambiguous",
    )


def _component_checks(
    root: Path,
    comp: Component,
    config: Config,
    sources_by_name: dict[str, Source],
    drivers: dict[str, Driver],
    indexes: dict[str, dict[tuple[ComponentType, str], Path]],
) -> list[Check]:
    """Checks for one enabled component: does its source exist, does it provide the
    artifact, does any active target install this type, and are its ``${VAR}`` refs set.

    Split out of :func:`run_checks` so that function reads as a list of check *categories*
    rather than one nested loop, and so the per-component rules can be tested directly.
    """
    src = sources_by_name.get(comp.source)
    if src is None:
        return [Check("error", "component", f"{comp.ref}: unknown source '{comp.source}'")]
    if comp.generate is not None:
        return []  # self-installing; artifact resolution doesn't apply

    artifact: Path | None
    if comp.path is not None:
        artifact = effective_root(root, src) / comp.path
    else:
        artifact = indexes.get(comp.source, {}).get((comp.type, comp.name))
    if artifact is None or not artifact.exists():
        return [Check("error", "component", f"{comp.ref}: not provided by source '{comp.source}'")]

    checks: list[Check] = []
    installs_into = [
        t
        for t in comp.applies_to(config.targets)
        if (d := drivers.get(t)) and d.supports(comp.type)
    ]
    if not installs_into:
        checks.append(
            Check("warn", "support", f"{comp.ref}: no active target installs a '{comp.type.value}'")
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
    return checks


def run_checks(root: Path) -> list[Check]:
    """Run all preflight checks for the project at ``root``. Read-only."""
    config = ConfigStore.load(root).parsed()
    checks: list[Check] = []
    name_check = command_name_check()
    if name_check is not None:
        checks.append(name_check)

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
        if comp.enabled:
            checks.extend(_component_checks(root, comp, config, sources_by_name, drivers, indexes))

    try:
        rows, _ = status(root)
        for r in rows:
            if r.state != "ok":
                checks.append(
                    Check("warn", "drift", f"{r.ref} → {r.target}: {r.state} (run `{prog()} sync`)")
                )
    except (ResolveError, deps.DependencyError):
        pass  # resolution errors already reported above

    if not checks:
        checks.append(Check("ok", "all", "all targets resolve, every component installs, no drift"))
    return checks
