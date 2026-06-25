"""The sync engine: make on-disk state match ``.agentry.yml`` + ``.agentry.lock``.

Flow:

1. Resolve every source into the store (honoring the lock unless ``update``).
2. Compute the *desired* set of installs (enabled components × applicable targets),
   resolving each component's artifact via that source's discovery index (descriptor
   or convention) and each target via the effective target map (built-in + profiles).
3. Diff against the *manifest* (what's currently installed) and apply the delta.
4. Persist lock + manifest, and ensure ``.agentry/`` is git-ignored.

Idempotent: running it twice changes nothing the second time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import deps, discovery
from .config import ConfigStore
from .gitignore import ensure_gitignore
from .installers import generate as gen_inst
from .installers import link as link_inst
from .installers import merge as merge_inst
from .lockfile import load_lock, save_lock
from .manifest import load_manifest, save_manifest
from .models import (
    ComponentType,
    Config,
    GeneratorSpec,
    InstalledGenerated,
    InstalledLink,
    InstalledMerge,
    Manifest,
    Strategy,
)
from .resolver import effective_root
from .targets import MergeDest, resolve_targets, unresolved_targets


@dataclass
class DesiredLink:
    component: str
    target: str
    path: str
    artifact: Path


@dataclass
class DesiredMerge:
    component: str
    target: str
    dest: MergeDest
    fragment: dict
    keys: list[str]


@dataclass
class DesiredGenerate:
    component: str
    target: str  # informational label (the component's applicable targets, joined)
    spec: GeneratorSpec


@dataclass
class SyncResult:
    resolved: dict[str, str] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gitignore_changed: bool = False


# -- desired-state computation -------------------------------------------


def compute_desired(
    root: Path, config: Config, warnings: list[str]
) -> tuple[list[DesiredLink], list[DesiredMerge], list[DesiredGenerate]]:
    specs = resolve_targets(config)
    for missing in unresolved_targets(config, specs):
        warnings.append(f"target '{missing}' is undefined — add it under target_profiles in .agentry.yml")

    # Build a (type, name) -> path index per source.
    indexes: dict[str, dict[tuple[ComponentType, str], Path]] = {}
    for src in config.sources:
        sp = effective_root(root, src)
        if sp.exists():
            indexes[src.name] = discovery.index(sp)

    links: list[DesiredLink] = []
    merges: list[DesiredMerge] = []
    generates: list[DesiredGenerate] = []

    for comp in config.components:
        if not comp.enabled:
            continue
        src = config.source(comp.source)
        if src is None:
            warnings.append(f"{comp.ref}: unknown source '{comp.source}'")
            continue
        if comp.generate is not None:
            # Self-installing component (GENERATE strategy) — artifact resolution does not apply.
            label = ", ".join(comp.applies_to(config.targets))
            generates.append(DesiredGenerate(comp.ref, label, comp.generate))
            continue
        if comp.path is not None:
            # Explicit artifact path: resolve directly under the source root, skipping discovery.
            artifact = effective_root(root, src) / comp.path
            if not artifact.exists():
                warnings.append(f"{comp.ref}: path '{comp.path}' not found in source '{comp.source}'")
                continue
        else:
            artifact = indexes.get(comp.source, {}).get((comp.type, comp.name))
            if artifact is None or not artifact.exists():
                warnings.append(f"{comp.ref}: not provided by source '{comp.source}'")
                continue

        for tname in comp.applies_to(config.targets):
            spec = specs.get(tname)
            if spec is None:
                continue  # already warned via unresolved_targets
            strat = spec.strategy(comp.type)
            if strat is None:
                warnings.append(f"{comp.ref}: target '{tname}' does not support {comp.type.value} — skipped")
                continue
            if strat is Strategy.LINK:
                links.append(DesiredLink(comp.ref, tname, spec.link_dest(comp.type, comp.name), artifact))
            else:
                dest = spec.merge_dest(comp.type)
                try:
                    entries = merge_inst.select_entries(merge_inst.load_fragment(artifact), dest)
                except (ValueError, OSError) as exc:
                    warnings.append(f"{comp.ref}: {exc}")
                    continue
                merges.append(DesiredMerge(comp.ref, tname, dest, entries, list(entries)))
    return links, merges, generates


# -- apply ---------------------------------------------------------------


def sync(root: Path, *, update: bool = False, allow_run: bool = False) -> SyncResult:
    store = ConfigStore.load(root)
    config = store.parsed()
    result = SyncResult()

    # 1. Resolve sources + the transitive dependency closure into the store.
    graph, lock = deps.resolve_graph(root, config, load_lock(root), update=update)
    result.resolved = graph.resolved
    result.warnings.extend(graph.warnings)
    save_lock(root, lock)

    # 2. Desired vs. installed. The augmented config carries synthesized sources and
    #    transitive components so reconcile treats them like any declared dependency.
    augmented = config.model_copy(update={"sources": graph.sources, "components": graph.components})
    links, merges, generates = compute_desired(root, augmented, result.warnings)
    manifest = load_manifest(root)

    _reconcile_links(root, links, manifest, result)
    _reconcile_merges(root, merges, manifest, result)
    _reconcile_generated(root, generates, manifest, result, allow_run=allow_run, update=update)

    save_manifest(root, manifest)

    # 3. Housekeeping.
    result.gitignore_changed = ensure_gitignore(root)
    return result


def _reconcile_links(root: Path, desired: list[DesiredLink], manifest: Manifest, result: SyncResult) -> None:
    desired_by_path = {d.path: d for d in desired}

    kept: list[InstalledLink] = []
    for inst in manifest.links:
        if inst.path not in desired_by_path:
            if link_inst.remove_link(root, inst.path):
                result.removed.append(f"link {inst.path}")
        else:
            kept.append(inst)
    manifest.links = kept

    have = {inst.path for inst in manifest.links}
    for path, d in desired_by_path.items():
        try:
            status = link_inst.install_link(root, d.artifact, path)
        except FileExistsError as exc:
            result.warnings.append(str(exc))
            continue
        if status == "created":
            result.created.append(f"link {path}")
        elif status == "updated":
            result.updated.append(f"link {path}")
        if path not in have:
            manifest.links.append(InstalledLink(component=d.component, target=d.target, path=path))
            have.add(path)


def _reconcile_merges(root: Path, desired: list[DesiredMerge], manifest: Manifest, result: SyncResult) -> None:
    def key(component: str, target: str) -> tuple[str, str]:
        return (component, target)

    desired_by_key = {key(d.component, d.target): d for d in desired}
    old_by_key = {key(m.component, m.target): m for m in manifest.merges}

    for k, m in old_by_key.items():
        if k not in desired_by_key:
            dest = MergeDest(m.file, m.pointer)
            if merge_inst.remove_merge(root, dest, m.keys):
                result.removed.append(f"merge {m.file}:{m.pointer} ({', '.join(m.keys)})")

    new_records: list[InstalledMerge] = []
    for k, d in desired_by_key.items():
        old = old_by_key.get(k)
        if old is not None:
            stale = [key_ for key_ in old.keys if key_ not in d.keys]
            if stale:
                merge_inst.remove_merge(root, d.dest, stale)
        existed = merge_inst.merge_state(root, d.dest, d.keys) == "ok"
        merge_inst.install_merge(root, d.dest, d.fragment)
        if not existed:
            result.created.append(f"merge {d.dest.file}:{d.dest.pointer} ({', '.join(d.keys)})")
        new_records.append(
            InstalledMerge(component=d.component, target=d.target, file=d.dest.file, pointer=d.dest.pointer, keys=d.keys)
        )
    manifest.merges = new_records


def _reconcile_generated(
    root: Path,
    desired: list[DesiredGenerate],
    manifest: Manifest,
    result: SyncResult,
    *,
    allow_run: bool,
    update: bool,
) -> None:
    desired_by_ref = {d.component: d for d in desired}
    old_by_ref = {g.component: g for g in manifest.generated}

    # Remove orphans first — uninstalling produced files runs no code, so it's always allowed.
    kept: list[InstalledGenerated] = []
    for ref, rec in old_by_ref.items():
        if ref not in desired_by_ref:
            removed = gen_inst.remove_generated(root, rec.paths)
            for p in removed:
                result.removed.append(f"generated {p}")
        else:
            kept.append(rec)
    manifest.generated = kept

    have = {g.component for g in manifest.generated}
    for ref, d in desired_by_ref.items():
        already = ref in have and gen_inst.produces_present(root, d.spec)
        if already and not update:
            continue  # idempotent: outputs present and tracked
        if not allow_run:
            cmds = "; ".join(gen_inst.describe(d.spec))
            result.warnings.append(
                f"{ref}: generator skipped — re-run with `agy sync --allow-run` to execute: {cmds}"
            )
            continue
        try:
            gen_inst.run_generator(root, d.spec)
        except gen_inst.GenerateError as exc:
            result.warnings.append(f"{ref}: {exc}")
            continue
        if ref not in have:
            manifest.generated.append(InstalledGenerated(component=ref, target=d.target, paths=list(d.spec.produces)))
            have.add(ref)
        result.created.append(f"generated {ref} ({', '.join(d.spec.produces)})")


# -- status (read-only drift report) -------------------------------------


@dataclass
class StatusRow:
    ref: str
    target: str
    where: str
    state: str  # ok | missing | drift


def status(root: Path) -> tuple[list[StatusRow], list[str]]:
    store = ConfigStore.load(root)
    config = store.parsed()
    warnings: list[str] = []
    graph, _ = deps.resolve_graph(root, config, load_lock(root))
    warnings.extend(graph.warnings)
    augmented = config.model_copy(update={"sources": graph.sources, "components": graph.components})
    links, merges, generates = compute_desired(root, augmented, warnings)

    rows: list[StatusRow] = []
    for d in links:
        rows.append(StatusRow(d.component, d.target, d.path, link_inst.link_state(root, d.artifact, d.path)))
    for d in merges:
        rows.append(
            StatusRow(d.component, d.target, f"{d.dest.file}:{d.dest.pointer}", merge_inst.merge_state(root, d.dest, d.keys))
        )
    for d in generates:
        where = ", ".join(d.spec.produces)
        state = "ok" if gen_inst.produces_present(root, d.spec) else "missing"
        rows.append(StatusRow(d.component, d.target, where, state))
    return rows, warnings
