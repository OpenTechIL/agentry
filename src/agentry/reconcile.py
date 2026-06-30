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
from .drivers import resolve_drivers
from .gitignore import ensure_gitignore
from .installers import copy as copy_inst
from .installers import generate as gen_inst
from .installers import link as link_inst
from .installers import link_merge as link_merge_inst
from .installers import merge as merge_inst
from .installers import transform as transform_inst
from .lockfile import load_lock, save_lock
from .manifest import load_manifest, save_manifest
from .models import (
    MERGE_TYPES,
    TYPE_IS_DIR,
    ComponentType,
    Config,
    GeneratorSpec,
    InstalledCopy,
    InstalledGenerated,
    InstalledLink,
    InstalledLinkMerge,
    InstalledMerge,
    InstalledTransform,
    Manifest,
    SourceType,
    Strategy,
)
from .resolver import effective_root
from .spec import LinkMergeDest, MergeDest
from .targets import unresolved_targets


@dataclass
class DesiredLink:
    component: str
    target: str
    path: str
    artifact: Path


@dataclass
class DesiredCopy:
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
class DesiredLinkMerge:
    component: str
    target: str
    link_path: str
    artifact: Path  # the script directory to symlink
    dest: MergeDest
    fragment: dict  # rewritten, unwrapped entries to merge
    keys: list[str]


@dataclass
class DesiredTransform:
    component: str
    target: str
    path: str  # destination (the target's link dest for this type)
    artifact: Path  # source file whose content is rewritten
    provider: str
    prompt: str | None


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
) -> tuple[
    list[DesiredLink],
    list[DesiredCopy],
    list[DesiredMerge],
    list[DesiredGenerate],
    list[DesiredLinkMerge],
    list[DesiredTransform],
]:
    drivers = resolve_drivers(config)
    for missing in unresolved_targets(config):
        warnings.append(
            f"target '{missing}' is undefined — define it under target_profiles in .agentry.yml, "
            "or install a shared driver overlay (`agy target list`)"
        )

    # Build a (type, name) -> path index per source.
    indexes: dict[str, dict[tuple[ComponentType, str], Path]] = {}
    for src in config.sources:
        sp = effective_root(root, src)
        if sp.exists():
            indexes[src.name] = discovery.index(sp)

    links: list[DesiredLink] = []
    copies: list[DesiredCopy] = []
    merges: list[DesiredMerge] = []
    generates: list[DesiredGenerate] = []
    link_merges: list[DesiredLinkMerge] = []
    transforms: list[DesiredTransform] = []

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
                warnings.append(
                    f"{comp.ref}: path '{comp.path}' not found in source '{comp.source}'"
                )
                continue
        else:
            artifact = indexes.get(comp.source, {}).get((comp.type, comp.name))
            if artifact is None or not artifact.exists():
                warnings.append(f"{comp.ref}: not provided by source '{comp.source}'")
                continue

        for tname in comp.applies_to(config.targets):
            driver = drivers.get(tname)
            if driver is None:
                continue  # already warned via unresolved_targets
            # Per-harness merge fragments (e.g. hooks-cursor.json) install only into the
            # matching target; skip foreign harnesses so a Cursor/Codex variant never
            # lands in another tool's config (e.g. Claude's settings.json).
            if comp.type in MERGE_TYPES:
                h = discovery.harness_suffix(comp.name)
                if h is not None and h != tname:
                    continue
            strat = driver.strategy(comp.type)
            if strat is None:
                warnings.append(
                    f"{comp.ref}: target '{tname}' does not support {comp.type.value} — skipped"
                )
                continue
            if comp.transform is not None:
                # Copy-with-rewrite: a transformed file replaces the live symlink. Only
                # single-file link types qualify; anything else installs normally with a note.
                if strat is Strategy.LINK and not TYPE_IS_DIR[comp.type]:
                    transforms.append(
                        DesiredTransform(
                            comp.ref,
                            tname,
                            driver.link_dest(comp.type, comp.name),
                            artifact,
                            comp.transform.provider,
                            comp.transform.prompt,
                        )
                    )
                    continue
                warnings.append(
                    f"{comp.ref}: transform is only supported for file components (agent/command) "
                    f"on link targets — installed without transform on '{tname}'"
                )
            if strat is Strategy.LINK:
                links.append(
                    DesiredLink(comp.ref, tname, driver.link_dest(comp.type, comp.name), artifact)
                )
            elif strat is Strategy.COPY:
                copies.append(
                    DesiredCopy(comp.ref, tname, driver.copy_dest(comp.type, comp.name), artifact)
                )
            elif strat is Strategy.LINK_MERGE:
                lm = _compute_link_merge(
                    comp, src, tname, driver.link_merge_dest(comp.type), artifact, warnings
                )
                if lm is not None:
                    link_merges.append(lm)
            else:
                dest = driver.merge_dest(comp.type)
                try:
                    entries = merge_inst.select_entries(merge_inst.load_fragment(artifact), dest)
                except (ValueError, OSError) as exc:
                    warnings.append(f"{comp.ref}: {exc}")
                    continue
                # Defense-in-depth: drop hook events the target doesn't recognize (e.g. Claude
                # Code rejects a settings.json carrying unknown events). No-op for agents
                # without a hook-event policy.
                if comp.type is ComponentType.HOOK:
                    entries, dropped = driver.filter_hook_events(entries)
                    for bad in dropped:
                        warnings.append(
                            f"{comp.ref}: hook event '{bad}' is not a recognized {tname} event — skipped"
                        )
                    if not entries:
                        continue
                    # A plugin-style hook ships ${...PLUGIN_ROOT} paths that only resolve inside
                    # a real installed plugin. Merged verbatim into a config file they fail at
                    # startup; the fix is a link+merge profile (which rewrites them). Warn rather
                    # than silently install a dead hook.
                    for cmd in link_merge_inst.plugin_root_refs(entries):
                        warnings.append(
                            f"{comp.ref}: hook command references a plugin-root variable "
                            f"({cmd!r}), which only resolves inside an installed plugin — merged "
                            f"into {dest.file} it will fail at startup. Configure a 'link+merge' "
                            f"hook profile under target_profiles for this repo (see the "
                            f"superpowers/arckit catalog entries)."
                        )
                # Seam: a driver.transform (currently always None) would reshape `entries`
                # here for agents needing semantic translation (e.g. JSON→TOML) before merge.
                merges.append(DesiredMerge(comp.ref, tname, dest, entries, list(entries)))
    return links, copies, merges, generates, link_merges, transforms


def _link_merge_vars(comp, src) -> dict[str, str]:
    """Path-template substitutions for a link+merge destination.

    ``{name}``   component name (e.g. ``hooks``)
    ``{source}`` the configured source/catalog name (e.g. ``arckit``)
    ``{repo}``   the source repo basename — git URL or local path tail (e.g. ``arc-kit``)
    ``{ref}``    the requested git ref, ``/`` flattened to ``-`` (e.g. ``main``)
    These let a profile namespace linked dirs per repo+ref —
    ``.claude/hooks/agentry/{repo}@{ref}/{name}`` — instead of colliding on ``{name}``.
    """
    locator = src.url if src.type is SourceType.GIT else src.path
    repo = comp.source
    if locator:
        repo = locator.rstrip("/").rsplit("/", 1)[-1]
        if repo.endswith(".git"):
            repo = repo[:-4]
    ref = (src.ref or "main").replace("/", "-")
    return {"name": comp.name, "source": comp.source, "repo": repo, "ref": ref}


def _expand(template: str, variables: dict[str, str]) -> str:
    """Substitute ``{key}`` placeholders by literal replacement.

    Not ``str.format``: rewrite targets often embed other ``${...}`` shell vars (e.g.
    ``${CLAUDE_PROJECT_DIR}``) that ``format`` would misread as fields.
    """
    for key, value in variables.items():
        template = template.replace("{" + key + "}", value)
    return template


def _compute_link_merge(
    comp, src, tname: str, lmdest: LinkMergeDest, artifact: Path, warnings: list[str]
):
    """Resolve a link+merge component: the script dir to link + the rewritten fragment.

    ``artifact`` is the script directory (``--path hooks``) or, if a file was resolved,
    its parent. The config to merge is ``<dir>/hooks.json`` (or the file itself).
    """
    if artifact.is_dir():
        link_src, config = artifact, artifact / "hooks.json"
    else:
        link_src, config = artifact.parent, artifact
    if not config.is_file():
        warnings.append(
            f"{comp.ref}: link+merge config '{config.name}' not found in '{comp.source}'"
        )
        return None
    try:
        entries = merge_inst.select_entries(merge_inst.load_fragment(config), lmdest.merge)
    except (ValueError, OSError) as exc:
        warnings.append(f"{comp.ref}: {exc}")
        return None
    variables = _link_merge_vars(comp, src)
    link_path = _expand(lmdest.link_dest, variables)
    rewrite_to = _expand(lmdest.rewrite_to, variables)
    rewritten, leftovers = link_merge_inst.rewrite_fragment(
        entries, lmdest.rewrite_from, rewrite_to
    )
    for cmd in leftovers:
        warnings.append(f"{comp.ref}: command not rewritten, may not resolve: {cmd}")
    return DesiredLinkMerge(
        comp.ref, tname, link_path, link_src, lmdest.merge, rewritten, list(rewritten)
    )


# -- apply ---------------------------------------------------------------


def sync(
    root: Path,
    *,
    update: bool = False,
    allow_run: bool = False,
    frozen: bool = False,
    allow_transform: bool = False,
) -> SyncResult:
    store = ConfigStore.load(root)
    config = store.parsed()
    result = SyncResult()

    # 1. Resolve sources + the transitive dependency closure into the store.
    graph, lock = deps.resolve_graph(root, config, load_lock(root), update=update, frozen=frozen)
    result.resolved = graph.resolved
    result.warnings.extend(graph.warnings)
    save_lock(root, lock)

    # 2. Desired vs. installed. The augmented config carries synthesized sources and
    #    transitive components so reconcile treats them like any declared dependency.
    augmented = config.model_copy(update={"sources": graph.sources, "components": graph.components})
    links, copies, merges, generates, link_merges, transforms = compute_desired(
        root, augmented, result.warnings
    )
    manifest = load_manifest(root)
    transform_cmd = config.transform.command if config.transform else []

    _reconcile_links(root, links, manifest, result)
    _reconcile_copies(root, copies, manifest, result)
    _reconcile_merges(root, merges, manifest, result)
    _reconcile_generated(root, generates, manifest, result, allow_run=allow_run, update=update)
    _reconcile_link_merges(root, link_merges, manifest, result)
    _reconcile_transforms(
        root, transforms, manifest, result, allow_transform=allow_transform, command=transform_cmd
    )

    save_manifest(root, manifest)

    # 3. Housekeeping.
    result.gitignore_changed = ensure_gitignore(root)
    return result


def _reconcile_links(
    root: Path, desired: list[DesiredLink], manifest: Manifest, result: SyncResult
) -> None:
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


def _reconcile_copies(
    root: Path, desired: list[DesiredCopy], manifest: Manifest, result: SyncResult
) -> None:
    desired_by_path = {d.path: d for d in desired}

    kept: list[InstalledCopy] = []
    for inst in manifest.copies:
        if inst.path not in desired_by_path:
            if copy_inst.remove_copy(root, inst.path):
                result.removed.append(f"copy {inst.path}")
        else:
            kept.append(inst)
    manifest.copies = kept

    have = {inst.path for inst in manifest.copies}
    for path, d in desired_by_path.items():
        try:
            status = copy_inst.install_copy(root, d.artifact, path, managed=path in have)
        except FileExistsError as exc:
            result.warnings.append(str(exc))
            continue
        if status == "created":
            result.created.append(f"copy {path}")
        elif status == "updated":
            result.updated.append(f"copy {path}")
        if path not in have:
            manifest.copies.append(InstalledCopy(component=d.component, target=d.target, path=path))
            have.add(path)


def _reconcile_transforms(
    root: Path,
    desired: list[DesiredTransform],
    manifest: Manifest,
    result: SyncResult,
    *,
    allow_transform: bool,
    command: list[str],
) -> None:
    from .emit import TransformError

    desired_by_path = {d.path: d for d in desired}

    kept: list[InstalledTransform] = []
    for inst in manifest.transforms:
        if inst.path not in desired_by_path:
            if copy_inst.remove_copy(root, inst.path):  # a transformed file is a managed real file
                result.removed.append(f"transform {inst.path}")
        else:
            kept.append(inst)
    manifest.transforms = kept

    have = {inst.path for inst in manifest.transforms}
    for path, d in desired_by_path.items():
        if d.provider == transform_inst.AGENT:
            if not allow_transform:
                result.warnings.append(
                    f"{d.component}: agent transform for {path} skipped — pass --allow-transform"
                )
                continue
            if path in have and (root / path).is_file():
                continue  # write-once: output exists; remove it to regenerate (non-reproducible)
            if not command:
                result.warnings.append(
                    f"{d.component}: no transform.command configured in .agentry.yml — skipped"
                )
                continue
        try:
            content = transform_inst.render(d.artifact, d.provider, d.prompt, command=command)
        except (TransformError, ValueError, OSError) as exc:
            result.warnings.append(f"{d.component}: transform failed: {exc}")
            continue
        try:
            status = transform_inst.install_transform(root, content, path, managed=path in have)
        except FileExistsError as exc:
            result.warnings.append(str(exc))
            continue
        if status == "created":
            result.created.append(f"transform {path}")
        elif status == "updated":
            result.updated.append(f"transform {path}")
        if path not in have:
            manifest.transforms.append(
                InstalledTransform(component=d.component, target=d.target, path=path)
            )
            have.add(path)


def _reconcile_merges(
    root: Path, desired: list[DesiredMerge], manifest: Manifest, result: SyncResult
) -> None:
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
            InstalledMerge(
                component=d.component,
                target=d.target,
                file=d.dest.file,
                pointer=d.dest.pointer,
                keys=d.keys,
            )
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
            manifest.generated.append(
                InstalledGenerated(component=ref, target=d.target, paths=list(d.spec.produces))
            )
            have.add(ref)
        result.created.append(f"generated {ref} ({', '.join(d.spec.produces)})")


def _reconcile_link_merges(
    root: Path, desired: list[DesiredLinkMerge], manifest: Manifest, result: SyncResult
) -> None:
    def key(component: str, target: str) -> tuple[str, str]:
        return (component, target)

    desired_by_key = {key(d.component, d.target): d for d in desired}
    old_by_key = {key(m.component, m.target): m for m in manifest.link_merges}

    # Remove orphans: both the symlink and the owned merge keys.
    for k, m in old_by_key.items():
        if k not in desired_by_key:
            removed = link_inst.remove_link(root, m.link_path)
            if merge_inst.remove_merge(root, MergeDest(m.file, m.pointer), m.keys) or removed:
                result.removed.append(f"link+merge {m.link_path} + {m.file}:{m.pointer}")

    new_records: list[InstalledLinkMerge] = []
    for k, d in desired_by_key.items():
        old = old_by_key.get(k)
        if old is not None:
            stale = [key_ for key_ in old.keys if key_ not in d.keys]
            if stale:
                merge_inst.remove_merge(root, d.dest, stale)
            # The dest template (e.g. {repo}@{ref}) may have moved the link — drop the old one.
            if old.link_path != d.link_path:
                link_inst.remove_link(root, old.link_path)
        try:
            link_status = link_inst.install_link(root, d.artifact, d.link_path)
        except FileExistsError as exc:
            result.warnings.append(str(exc))
            continue
        merge_existed = merge_inst.merge_state(root, d.dest, d.keys) == "ok"
        merge_inst.install_merge(root, d.dest, d.fragment)
        if link_status == "created" or not merge_existed:
            result.created.append(f"link+merge {d.link_path} + {d.dest.file}:{d.dest.pointer}")
        elif link_status == "updated":
            result.updated.append(f"link+merge {d.link_path}")
        new_records.append(
            InstalledLinkMerge(
                component=d.component,
                target=d.target,
                link_path=d.link_path,
                file=d.dest.file,
                pointer=d.dest.pointer,
                keys=d.keys,
            )
        )
    manifest.link_merges = new_records


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
    links, copies, merges, generates, link_merges, transforms = compute_desired(
        root, augmented, warnings
    )

    rows: list[StatusRow] = []
    for d in links:
        rows.append(
            StatusRow(d.component, d.target, d.path, link_inst.link_state(root, d.artifact, d.path))
        )
    for d in copies:
        rows.append(
            StatusRow(d.component, d.target, d.path, copy_inst.copy_state(root, d.artifact, d.path))
        )
    for d in merges:
        rows.append(
            StatusRow(
                d.component,
                d.target,
                f"{d.dest.file}:{d.dest.pointer}",
                merge_inst.merge_state(root, d.dest, d.keys),
            )
        )
    for d in generates:
        where = ", ".join(d.spec.produces)
        state = "ok" if gen_inst.produces_present(root, d.spec) else "missing"
        rows.append(StatusRow(d.component, d.target, where, state))
    for d in link_merges:
        link_ok = link_inst.link_state(root, d.artifact, d.link_path) == "ok"
        merge_ok = merge_inst.merge_state(root, d.dest, d.keys) == "ok"
        state = "ok" if (link_ok and merge_ok) else "missing"
        rows.append(
            StatusRow(
                d.component, d.target, f"{d.link_path} + {d.dest.file}:{d.dest.pointer}", state
            )
        )
    for d in transforms:
        rows.append(
            StatusRow(d.component, d.target, d.path, transform_inst.transform_state(root, d.path))
        )
    return rows, warnings
