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

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

from . import deps, discovery, envscan
from .config import ConfigStore
from .drivers import Driver, resolve_drivers
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
    MANIFEST_VERSION,
    MERGE_TYPES,
    TYPE_IS_DIR,
    Component,
    ComponentType,
    Config,
    GeneratorSpec,
    InstalledCopy,
    InstalledGenerated,
    InstalledLink,
    InstalledLinkMerge,
    InstalledMerge,
    InstalledTransform,
    Lock,
    Manifest,
    OwnedEntry,
    Source,
    SourceType,
    Strategy,
    parse_keep,
)
from .naming import repo_basename
from .progname import prog
from .resolver import effective_root
from .spec import LinkMergeDest, MergeDest, check_format
from .targets import check_targets, unresolved_targets


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
    kept: frozenset[str] = frozenset()  # the component's ``keep:`` keys for this target
    bare: frozenset[str] = frozenset()  # keys whose keep hint is bare (see MergeContext.bare)


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
    kept: frozenset[str] = frozenset()  # the component's ``keep:`` keys for this target
    bare: frozenset[str] = frozenset()  # see DesiredMerge.bare


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
    # Repetitive warnings the CLI may collapse into one summary line, by kind
    # (``"unsupported"``: type/target skips; ``"drift"``: W2). Every string here is also in
    # ``warnings``, which stays the complete list.
    summarized: dict[str, list[str]] = field(default_factory=dict)


# -- desired-state computation -------------------------------------------


@dataclass
class DesiredPlan:
    """What *should* be installed, grouped by strategy.

    One field per installer. Named rather than a 6-tuple so callers (``sync``, ``status``)
    can't transpose two same-typed lists, and so adding a seventh strategy doesn't rewrite
    every unpack site.
    """

    links: list[DesiredLink] = field(default_factory=list)
    copies: list[DesiredCopy] = field(default_factory=list)
    merges: list[DesiredMerge] = field(default_factory=list)
    generates: list[DesiredGenerate] = field(default_factory=list)
    link_merges: list[DesiredLinkMerge] = field(default_factory=list)
    transforms: list[DesiredTransform] = field(default_factory=list)
    #: "target does not support <type> — skipped" warnings (also in the caller's warnings),
    #: kept apart so the CLI can summarize them.
    skipped: list[str] = field(default_factory=list)
    #: Cross-target format mismatches (phase A, also in the caller's warnings): one message
    #: per (component, target) whose content the target's ``expect`` says won't work there.
    format_issues: list[str] = field(default_factory=list)
    #: ``(canonical ref, target) -> variant ref``: a suffix-less merge fragment that a
    #: same-source per-harness variant replaces on that target (routing, so no row there).
    replaced: dict[tuple[str, str], str] = field(default_factory=dict)


def compute_desired(root: Path, config: Config, warnings: list[str]) -> DesiredPlan:
    """Resolve config + on-disk sources into the full desired install plan.

    Per component: resolve its artifact (explicit ``path``, discovery index, or the
    self-installing ``generate`` case), warn about dead ``${VAR}`` placeholders, then hand
    each of its targets to :func:`_desired_for_target` for strategy dispatch.
    """
    drivers = resolve_drivers(config)
    for missing in unresolved_targets(config):
        warnings.append(
            f"target '{missing}' is undefined — define it under target_profiles in .agentry.yml, "
            f"or install a shared driver overlay (`{prog()} target list`)"
        )

    # Build a (type, name) -> path index per source.
    indexes: dict[str, dict[tuple[ComponentType, str], Path]] = {}
    for indexed in config.sources:
        sp = effective_root(root, indexed)
        if sp.exists():
            indexes[indexed.name] = discovery.index(sp)

    plan = DesiredPlan()
    variants = _enabled_variants(config)

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
            plan.generates.append(DesiredGenerate(comp.ref, label, comp.generate))
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
            found = indexes.get(comp.source, {}).get((comp.type, comp.name))
            if found is None or not found.exists():
                warnings.append(f"{comp.ref}: not provided by source '{comp.source}'")
                continue
            artifact = found

        # agentry ships ${VAR} placeholders verbatim (the runtime resolves them), but a
        # reference that is unset *and* has no default would ship dead. Warn once per
        # component rather than silently install it — mirrors `agentry doctor`.
        if comp.type in MERGE_TYPES and artifact.is_file():
            for var in envscan.unset_env_refs(artifact.read_text(encoding="utf-8")):
                warnings.append(
                    f"{comp.ref}: references ${{{var}}}, which is unset — set it before your "
                    "agent runs (agentry ships the placeholder; the runtime resolves it)"
                )

        for tname in comp.applies_to(config.targets):
            driver = drivers.get(tname)
            if driver is None:
                continue  # already warned via unresolved_targets
            _desired_for_target(comp, src, tname, driver, artifact, plan, warnings, variants)

    _warn_unmatched_keep(config, plan, warnings)
    return plan


def _warn_unmatched_keep(config: Config, plan: DesiredPlan, warnings: list[str]) -> None:
    """One warning per ``keep:`` entry that matches no key the component merges (a typo)."""
    merged: dict[str, set[tuple[str, str]]] = {}
    for dm in plan.merges:
        merged.setdefault(dm.component, set()).update((dm.target, k) for k in dm.fragment)
    for dlm in plan.link_merges:
        merged.setdefault(dlm.component, set()).update((dlm.target, k) for k in dlm.fragment)
    for comp in config.components:
        if not comp.enabled or not comp.keep:
            continue
        have = merged.get(comp.ref, set())
        for entry in comp.keep:
            scope, key = parse_keep(entry, comp.ref)
            if not any(k == key and (scope is None or t == scope) for t, k in have):
                warnings.append(
                    f"{comp.ref}: keep entry '{entry}' matches no key this component merges — "
                    "check the spelling (keep: takes merged entry names, optionally "
                    "'<target>:<key>')"
                )


def _enabled_variants(config: Config) -> frozenset[tuple[str, ComponentType, str, str]]:
    """``(source, type, base, harness)`` for every enabled per-harness merge variant
    (``<base>-<harness>``) that applies to its own harness target."""
    out: set[tuple[str, ComponentType, str, str]] = set()
    for comp in config.components:
        if not comp.enabled or comp.type not in MERGE_TYPES:
            continue
        h = discovery.harness_suffix(comp.name)
        if h is not None and h in comp.applies_to(config.targets):
            out.add((comp.source, comp.type, comp.name.rpartition("-")[0], h))
    return frozenset(out)


def _routes_to(
    comp: Component, tname: str, variants: frozenset[tuple[str, ComponentType, str, str]]
) -> bool:
    """Per-harness routing for merge fragments: does ``comp`` belong on ``tname`` at all?

    A ``<base>-<harness>`` variant installs only into its own harness target. The
    suffix-less canonical file applies to every *other* target: where the same source has
    an enabled variant for this target, the variant replaces it (routing, not a conflict —
    so silent, and no status row there). Non-merge types always route.
    """
    if comp.type not in MERGE_TYPES:
        return True
    h = discovery.harness_suffix(comp.name)
    if h is not None:
        return h == tname
    return (comp.source, comp.type, comp.name, tname) not in variants


def _desired_for_target(
    comp: Component,
    src: Source,
    tname: str,
    driver: Driver,
    artifact: Path,
    plan: DesiredPlan,
    warnings: list[str],
    variants: frozenset[tuple[str, ComponentType, str, str]] = frozenset(),
) -> None:
    """Dispatch one (component, target) pair to the right strategy, appending to ``plan``.

    Split out of :func:`compute_desired` so the strategy dispatch reads on its own, without
    the surrounding artifact-resolution loop. Appends nothing when the pair doesn't install
    (unsupported type, a foreign per-harness variant, an unreadable fragment).
    """
    # Per-harness merge fragments (e.g. hooks-cursor.json) install only into the
    # matching target; skip foreign harnesses so a Cursor/Codex variant never
    # lands in another tool's config (e.g. Claude's settings.json).
    if not _routes_to(comp, tname, variants):
        if discovery.harness_suffix(comp.name) is None:  # a canonical, replaced here
            plan.replaced[(comp.ref, tname)] = (
                f"{comp.source}/{comp.type.value}/{comp.name}-{tname}"
            )
        return
    strat = driver.strategy(comp.type)
    if strat is None:
        msg = f"{comp.ref}: target '{tname}' does not support {comp.type.value} — skipped"
        warnings.append(msg)
        plan.skipped.append(msg)
        return
    if comp.type not in MERGE_TYPES and comp.transform is None:
        # A transformed component installs the rewritten output, not this artifact, so
        # the source file's format says nothing about what lands.
        _check_format(comp, tname, driver, plan, warnings, artifact=artifact)
    if comp.transform is not None:
        # Copy-with-rewrite: a transformed file replaces the live symlink. Only
        # single-file link types qualify; anything else installs normally with a note.
        if strat is Strategy.LINK and not TYPE_IS_DIR[comp.type]:
            plan.transforms.append(
                DesiredTransform(
                    comp.ref,
                    tname,
                    driver.link_dest(comp.type, comp.name),
                    artifact,
                    comp.transform.provider,
                    comp.transform.prompt,
                )
            )
            return
        warnings.append(
            f"{comp.ref}: transform is only supported for file components (agent/command) "
            f"on link targets — installed without transform on '{tname}'"
        )
    if strat is Strategy.LINK:
        plan.links.append(
            DesiredLink(comp.ref, tname, driver.link_dest(comp.type, comp.name), artifact)
        )
    elif strat is Strategy.COPY:
        plan.copies.append(
            DesiredCopy(comp.ref, tname, driver.copy_dest(comp.type, comp.name), artifact)
        )
    elif strat is Strategy.LINK_MERGE:
        lm = _compute_link_merge(
            comp, src, tname, driver.link_merge_dest(comp.type), artifact, warnings
        )
        if lm is not None:
            _check_format(comp, tname, driver, plan, warnings, entries=lm.fragment)
            plan.link_merges.append(lm)
    else:
        dest = driver.merge_dest(comp.type)
        try:
            entries = merge_inst.select_entries(merge_inst.load_fragment(artifact), dest)
        except (ValueError, OSError) as exc:
            warnings.append(f"{comp.ref}: {exc}")
            return
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
                return
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
        _check_format(comp, tname, driver, plan, warnings, entries=entries)
        # Seam: a driver.transform (currently always None) would reshape `entries`
        # here for agents needing semantic translation (e.g. JSON→TOML) before merge.
        plan.merges.append(
            DesiredMerge(comp.ref, tname, dest, entries, list(entries), comp.kept_keys(tname))
        )


def _check_format(
    comp: Component,
    tname: str,
    driver: Driver,
    plan: DesiredPlan,
    warnings: list[str],
    *,
    artifact: Path | None = None,
    entries: dict | None = None,
) -> None:
    """Warn (once per component/target) when content won't work in the target (phase A).

    The bytes still install unchanged; the expectation is driver/profile data
    (:class:`~agentry.spec.FormatExpectation`), so no tool name is special-cased here.
    """
    exp = driver.spec.expect.get(comp.type)
    if exp is None:
        return
    problems = check_format(exp, artifact=artifact, entries=entries)
    if not problems:
        return
    wants = f" (expects {exp.expects})" if exp.expects else ""
    msg = (
        f"{comp.ref}: may not work in {tname}{wants}: {'; '.join(problems)} — installed as-is. "
        f"Ship {'an' if tname[:1] in 'aeiou' else 'a'} {tname}-specific {comp.type.value}, "
        f"or override {comp.type.value} for "
        f"{tname} under target_profiles"
    )
    warnings.append(msg)
    plan.format_issues.append(msg)


def _link_merge_vars(comp: Component, src: Source) -> dict[str, str]:
    """Path-template substitutions for a link+merge destination.

    ``{name}``   component name (e.g. ``hooks``)
    ``{source}`` the configured source/catalog name (e.g. ``arckit``)
    ``{repo}``   the source repo basename — git URL or local path tail (e.g. ``arc-kit``)
    ``{ref}``    the requested git ref, ``/`` flattened to ``-`` (e.g. ``main``)
    These let a profile namespace linked dirs per repo+ref —
    ``.claude/hooks/agentry/{repo}@{ref}/{name}`` — instead of colliding on ``{name}``.
    """
    locator = src.url if src.type is SourceType.GIT else src.path
    repo = repo_basename(locator, fallback=comp.source) if locator else comp.source
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
    comp: Component,
    src: Source,
    tname: str,
    lmdest: LinkMergeDest,
    artifact: Path,
    warnings: list[str],
) -> DesiredLinkMerge | None:
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
        comp.ref,
        tname,
        link_path,
        link_src,
        lmdest.merge,
        rewritten,
        list(rewritten),
        comp.kept_keys(tname),
    )


# -- apply ---------------------------------------------------------------


# Decide consent for a code-executing source the first time sync hits it. Receives
# ``(source_name, resolved_sha)`` and returns True to grant (and persist) trust. The CLI
# supplies an interactive prompt; tests/non-interactive callers pass None (→ no consent).
TrustCallback = Callable[[str, str], bool]


def sync(
    root: Path,
    *,
    update: bool = False,
    allow_run: bool = False,
    frozen: bool = False,
    allow_transform: bool = False,
    trust_callback: TrustCallback | None = None,
) -> SyncResult:
    store = ConfigStore.load(root)
    config = store.parsed()
    # 0. Validate targets — a pure config check, before any network call or write: a typo'd
    #    target would otherwise resolve to nothing and uninstall the real one's components.
    check_targets(config)
    result = SyncResult()

    # 1. Resolve sources + the transitive dependency closure into the store.
    graph, lock = deps.resolve_graph(root, config, load_lock(root), update=update, frozen=frozen)
    result.resolved = graph.resolved
    result.warnings.extend(graph.warnings)
    save_lock(root, lock)

    # 2. Desired vs. installed. The augmented config carries synthesized sources and
    #    transitive components so reconcile treats them like any declared dependency.
    augmented = config.model_copy(update={"sources": graph.sources, "components": graph.components})
    plan = compute_desired(root, augmented, result.warnings)
    result.summarized["unsupported"] = list(plan.skipped)
    result.summarized["format"] = list(plan.format_issues)
    links, copies, merges = plan.links, plan.copies, plan.merges
    generates, link_merges, transforms = plan.generates, plan.link_merges, plan.transforms
    manifest = load_manifest(root)
    transform_cmd = config.transform.command if config.transform else []

    _reconcile_links(root, links, manifest, result)
    _reconcile_copies(root, copies, manifest, result)
    trust_changed = _reconcile_generated(
        root,
        generates,
        manifest,
        result,
        allow_run=allow_run,
        update=update,
        lock=lock,
        trust_callback=trust_callback,
    )
    # One pass over every merge destination, after generators (ADR-001 §8).
    _reconcile_merge_targets(
        root, augmented, config, merges, link_merges, manifest, result, update=update
    )
    _reconcile_transforms(
        root, transforms, manifest, result, allow_transform=allow_transform, command=transform_cmd
    )

    manifest.version = MANIFEST_VERSION
    save_manifest(root, manifest)
    if trust_changed:
        save_lock(root, lock)  # persist newly-granted per-source consent

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


def _warn_unparseable(
    result: SyncResult, component: str, action: str, exc: merge_inst.MergeTargetError
) -> None:
    """Report a merge skipped because its destination file can't be parsed."""
    result.warnings.append(f"{component}: {action} its config — {exc}")


Who = tuple[str, str]  # (component ref, target) — the identity of one merge record


class _Claims:
    """Which record owns each ``(file, pointer, key)`` — so no component writes over, or
    adopts, an entry another live component installed (ADR-001)."""

    def __init__(self, records: list[InstalledMerge | InstalledLinkMerge]) -> None:
        self._by: dict[tuple[str, str, str], Who] = {}
        for r in records:
            self.claim((r.component, r.target), r.file, r.pointer, r.entries)

    def claim(self, who: Who, file: str, pointer: str, entries: dict[str, OwnedEntry]) -> None:
        for key, entry in entries.items():
            if not entry.preexisting:
                self._by[(file, pointer, key)] = who

    def release(self, who: Who) -> None:
        self._by = {k: v for k, v in self._by.items() if v != who}

    def lookup(self, who: Who, file: str, pointer: str) -> Callable[[str], str | None]:
        def other(key: str) -> str | None:
            owner = self._by.get((file, pointer, key))
            return owner[0] if owner is not None and owner != who else None

        return other


#: Best-effort lookup of a record's current source fragment (``None`` if unresolvable).
FragmentLookup = Callable[[InstalledMerge | InstalledLinkMerge], dict | None]


def _migrate_v1(
    docs: merge_inst.Documents,
    records: list[InstalledMerge | InstalledLinkMerge],
    desired: dict[Who, tuple[MergeDest, dict]],
    fallback: FragmentLookup,
) -> None:
    """Give legacy v1 records (no ``entries``) named-mode ownership (see ``migrate_named``).

    A list-valued hook key is owned only when it still equals what the component installs:
    the fragment comes from the desired plan, or — for a record no longer in the plan (being
    disabled, or a foreign-harness leftover) — from ``fallback``, which re-reads the
    source. In memory only; an unparseable file is left for the caller to report.
    """
    for r in records:
        if r.entries or not r.keys:
            continue
        try:
            doc = docs.get(r.file)
        except merge_inst.MergeTargetError:
            continue
        want = desired.get((r.component, r.target))
        if want is not None and (want[0].file, want[0].pointer) == (r.file, r.pointer):
            fragment: dict | None = want[1]
        else:
            fragment = fallback(r)
        r.entries = merge_inst.migrate_named(doc, r.pointer, r.keys, fragment)


def _desired_fragments(
    merges: list[DesiredMerge], link_merges: list[DesiredLinkMerge]
) -> dict[Who, tuple[MergeDest, dict]]:
    out: dict[Who, tuple[MergeDest, dict]] = {}
    for dm in merges:
        out[(dm.component, dm.target)] = (dm.dest, dm.fragment)
    for dlm in link_merges:
        out[(dlm.component, dlm.target)] = (dlm.dest, dlm.fragment)
    return out


def _source_fragments(root: Path, config: Config, declared: Config) -> FragmentLookup:
    """Re-derive a v1 record's fragment from its source (v1 migration fallback).

    A plain merge record re-reads its fragment file; a link+merge record re-runs
    :func:`_compute_link_merge` (forced-enabled), so its fragment carries the same rewritten
    command paths the plan would install. ``config`` is the resolved (augmented) config,
    whose sources include synthesized ones; ``declared`` is the user's config, which still
    lists disabled components (their ``path`` overrides matter here). ``None`` when the
    record's source or artifact can't be resolved — its list keys then stay unproven.
    """
    indexes: dict[str, dict[tuple[ComponentType, str], Path]] = {}
    drivers = resolve_drivers(config)

    def lookup(rec: InstalledMerge | InstalledLinkMerge) -> dict | None:
        parts = rec.component.split("/", 2)
        if len(parts) != 3:
            return None
        try:
            ctype = ComponentType(parts[1])
        except ValueError:
            return None
        src = config.source(parts[0])
        if src is None:
            return None
        base = effective_root(root, src)
        comp = next(
            (c for c in [*config.components, *declared.components] if c.ref == rec.component),
            None,
        ) or Component(source=parts[0], type=ctype, name=parts[2])
        if comp.path is not None:
            artifact: Path | None = base / comp.path
        else:
            if src.name not in indexes:
                indexes[src.name] = discovery.index(base) if base.exists() else {}
            artifact = indexes[src.name].get((ctype, parts[2]))
        if artifact is None or not artifact.exists():
            return None
        if isinstance(rec, InstalledLinkMerge):
            driver = drivers.get(rec.target)
            if driver is None or driver.strategy(ctype) is not Strategy.LINK_MERGE:
                return None
            lm = _compute_link_merge(
                comp, src, rec.target, driver.link_merge_dest(ctype), artifact, []
            )
            if lm is None or (lm.dest.file, lm.dest.pointer) != (rec.file, rec.pointer):
                return None
            return lm.fragment
        if not artifact.is_file():
            return None
        try:
            return merge_inst.select_entries(
                merge_inst.load_fragment(artifact), MergeDest(rec.file, rec.pointer)
            )
        except (ValueError, OSError):
            return None

    return lookup


def _ctx(
    who: Who, file: str, pointer: str, bare: frozenset[str] = frozenset()
) -> merge_inst.MergeContext:
    return merge_inst.MergeContext(ref=who[0], target=who[1], file=file, pointer=pointer, bare=bare)


def _differs(rec: OwnedEntry | None, have: str, want: str) -> bool:
    """Whether a recorded entry is one the user now owns a different value of (W2/W1b)."""
    if rec is None:
        return False
    if rec.preexisting:
        return have != want
    return rec.fingerprint != have


def _bare_keys(seen: dict[tuple[str, str], tuple[set[str], set[str]]]) -> dict[str, set[str]]:
    """``(component, key) -> (targets written, targets drifted)`` ⇒ ``component -> bare keys``.

    A key's keep hint is bare only when it drifted on every target the component writes it
    to; otherwise the hint names the drifted target, so keep never freezes a healthy one.
    """
    out: dict[str, set[str]] = {}
    for (ref, key), (written, drifted) in seen.items():
        if written and drifted == written:
            out.setdefault(ref, set()).add(key)
    return out


def _mark_bare_desired(
    docs: merge_inst.Documents,
    desired: list[DesiredMerge] | list[DesiredLinkMerge],
    old_records: dict[Who, InstalledMerge | InstalledLinkMerge],
    seen: dict[tuple[str, str], tuple[set[str], set[str]]],
) -> None:
    """Accumulate, per (component, key), which targets write it and which drifted."""
    for d in desired:
        try:
            doc = docs.get(d.dest.file)
        except merge_inst.MergeTargetError:
            continue
        section = doc.get(d.dest.pointer)
        current = section if isinstance(section, MutableMapping) else {}
        rec = old_records.get((d.component, d.target))
        same = rec is not None and (rec.file, rec.pointer) == (d.dest.file, d.dest.pointer)
        owned = rec.entries if rec is not None and same else {}
        for key, value in d.fragment.items():
            if key in d.kept:
                continue
            written, drifted = seen.setdefault((d.component, key), (set(), set()))
            written.add(d.target)
            if key in current and _differs(
                owned.get(key),
                merge_inst.fingerprint(current[key]),
                merge_inst.fingerprint(value),
            ):
                drifted.add(d.target)


def _assign_bare(
    merges: list[DesiredMerge],
    link_merges: list[DesiredLinkMerge],
    docs: merge_inst.Documents,
    records: list[InstalledMerge | InstalledLinkMerge],
) -> None:
    """Set each desired merge's ``bare`` keep-hint keys from the cross-target drift state."""
    old = {(r.component, r.target): r for r in records}
    seen: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    _mark_bare_desired(docs, merges, old, seen)
    _mark_bare_desired(docs, link_merges, old, seen)
    bare = _bare_keys(seen)
    for dm in merges:
        dm.bare = frozenset(bare.get(dm.component, set()))
    for dlm in link_merges:
        dlm.bare = frozenset(bare.get(dlm.component, set()))


def _orphan_bare(
    docs: merge_inst.Documents, orphans: list[InstalledMerge] | list[InstalledLinkMerge]
) -> dict[str, set[str]]:
    """Bare keep-hint keys for records being removed (W4): edited on every target they had."""
    seen: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    for r in orphans:
        try:
            doc = docs.get(r.file)
        except merge_inst.MergeTargetError:
            continue
        section = doc.get(r.pointer)
        current = section if isinstance(section, MutableMapping) else {}
        for key, e in r.entries.items():
            if e.preexisting:
                continue
            written, drifted = seen.setdefault((r.component, key), (set(), set()))
            written.add(r.target)
            if key in current and (
                e.fingerprint is None or e.fingerprint != merge_inst.fingerprint(current[key])
            ):
                drifted.add(r.target)
    return _bare_keys(seen)


def _remove_record(
    docs: merge_inst.Documents,
    rec: InstalledMerge | InstalledLinkMerge,
    result: SyncResult,
    kept: frozenset[str] = frozenset(),
    bare: frozenset[str] = frozenset(),
) -> bool | None:
    """Remove a record's owned entries, never its ``kept`` keys (the user's by ``keep:``).

    ``None`` when its file can't be parsed (the record is kept for a later retry).
    """
    try:
        doc = docs.get(rec.file)
    except merge_inst.MergeTargetError as exc:
        _warn_unparseable(result, rec.component, "not removed from", exc)
        return None
    unverified = None
    if isinstance(rec, InstalledLinkMerge):
        link_path = rec.link_path

        def unverified(key: str) -> str:  # W6: hook commands may now point at nothing
            return (
                f"{rec.component}: '{rec.pointer}.{key}' in {rec.file} was installed by an "
                "older agentry with link+merge and can't be verified — left in place on "
                f"removal; its commands may point at the removed {link_path}. Delete them by "
                "hand if unused."
            )

    removed, warnings = merge_inst.remove_named(
        doc,
        _ctx((rec.component, rec.target), rec.file, rec.pointer, bare),
        {k: e for k, e in rec.entries.items() if k not in kept},
        unverified,
    )
    result.warnings.extend(warnings)
    return removed


def _plan_record(
    docs: merge_inst.Documents,
    who: Who,
    dest: MergeDest,
    fragment: dict,
    old: InstalledMerge | InstalledLinkMerge | None,
    claims: _Claims,
    result: SyncResult,
    kept: frozenset[str] = frozenset(),
    bare: frozenset[str] = frozenset(),
) -> tuple[merge_inst.NamedPlan, dict[str, OwnedEntry]] | None:
    """Plan one desired merge against its document. ``None`` when the file can't be parsed.

    Returns the plan plus the stale entries (owned before, no longer in the fragment) to
    remove when the plan is committed. Nothing is mutated, so a refused plan leaves the old
    record (and its entries on disk) exactly as they were.
    """
    try:
        doc = docs.get(dest.file)
    except merge_inst.MergeTargetError as exc:
        _warn_unparseable(result, who[0], "not merged into", exc)
        return None
    moved = old is not None and (old.file, old.pointer) != (dest.file, dest.pointer)
    owned = dict(old.entries) if old is not None and not moved else {}
    stale = {k: e for k, e in owned.items() if k not in fragment and k not in kept}
    plan = merge_inst.plan_named(
        doc,
        _ctx(who, dest.file, dest.pointer, bare),
        fragment,
        owned,
        claims.lookup(who, dest.file, dest.pointer),
        kept,
        installed=old is not None and not moved,  # per-key refusal once installed here
    )
    result.warnings.extend(plan.warnings)
    result.summarized.setdefault("drift", []).extend(plan.drift_warnings)
    result.summarized.setdefault("kept", []).extend(plan.kept_missing_warnings)
    return plan, stale


def _commit_record(
    docs: merge_inst.Documents,
    who: Who,
    dest: MergeDest,
    old: InstalledMerge | InstalledLinkMerge | None,
    plan: merge_inst.NamedPlan,
    stale: dict[str, OwnedEntry],
    claims: _Claims,
    result: SyncResult,
    kept: frozenset[str] = frozenset(),
    bare: frozenset[str] = frozenset(),
) -> None:
    """Apply a non-refused plan: drop a moved record's old entries, then stale keys, then
    the writes; re-point this record's claims at what it now owns."""
    if old is not None and (old.file, old.pointer) != (dest.file, dest.pointer):
        # Destination moved: never orphan the old entries (kept keys stay the user's).
        _remove_record(docs, old, result, kept, bare)
    doc = docs.get(dest.file)
    ctx = _ctx(who, dest.file, dest.pointer, bare)
    if stale:
        _, warnings = merge_inst.remove_named(doc, ctx, stale)
        result.warnings.extend(warnings)
    merge_inst.commit_named(doc, dest.pointer, plan)
    claims.release(who)
    claims.claim(who, dest.file, dest.pointer, plan.entries)


def _reconcile_merge_targets(
    root: Path,
    config: Config,
    declared: Config,
    merges: list[DesiredMerge],
    link_merges: list[DesiredLinkMerge],
    manifest: Manifest,
    result: SyncResult,
    *,
    update: bool = False,
) -> None:
    """The single file-grouped merge pass (ADR-001 §8): merges, then link+merges.

    Every destination is parsed once into a shared :class:`~merge_inst.Documents` cache,
    each component is evaluated against it atomically (refused ⇒ nothing written, record
    kept), and each changed file is written once at the end. Runs after generators, so
    conflicts are judged against whatever a generator wrote.
    """
    docs = merge_inst.Documents(root)
    records: list[InstalledMerge | InstalledLinkMerge] = [*manifest.merges, *manifest.link_merges]
    if update and any(r.keys and not r.entries for r in records):
        # Migration verifies v1 entries against the fragment being installed; after a
        # re-resolve that is the *new* revision, so unchanged entries can't be proven.
        result.warnings.append(
            f"manifest from an older agentry: run `{prog()} sync` before `{prog()} update` so "
            "installed entries can be verified"
        )
    _migrate_v1(
        docs,
        records,
        _desired_fragments(merges, link_merges),
        _source_fragments(root, config, declared),
    )
    claims = _Claims(records)
    _assign_bare(merges, link_merges, docs, records)
    kept_for = _kept_lookup(config, declared)
    _reconcile_merges(docs, merges, manifest, result, claims, kept_for)
    _reconcile_link_merges(root, docs, link_merges, manifest, result, claims, kept_for)
    docs.flush()


def _kept_lookup(config: Config, declared: Config) -> Callable[[Who], frozenset[str]]:
    """``(component, target) -> keep: keys`` — including disabled/removed-from-plan components,
    so a component being uninstalled still never removes a key the user keeps."""
    comps = {c.ref: c for c in [*declared.components, *config.components]}

    def lookup(who: Who) -> frozenset[str]:
        comp = comps.get(who[0])
        return comp.kept_keys(who[1]) if comp is not None else frozenset()

    return lookup


def _reconcile_merges(
    docs: merge_inst.Documents,
    desired: list[DesiredMerge],
    manifest: Manifest,
    result: SyncResult,
    claims: _Claims,
    kept_for: Callable[[Who], frozenset[str]],
) -> None:
    desired_by_key = {(d.component, d.target): d for d in desired}
    old_by_key = {(m.component, m.target): m for m in manifest.merges}
    orphan_bare = _orphan_bare(docs, [m for k, m in old_by_key.items() if k not in desired_by_key])

    new_records: list[InstalledMerge] = []
    for k, m in old_by_key.items():
        if k in desired_by_key:
            continue
        removed = _remove_record(
            docs, m, result, kept_for(k), frozenset(orphan_bare.get(m.component, set()))
        )
        if removed is None:
            new_records.append(m)  # unparseable: keep it so removal is retried
            continue
        claims.release(k)
        if removed:
            result.removed.append(f"merge {m.file}:{m.pointer} ({', '.join(m.keys)})")

    for k, d in desired_by_key.items():
        old = old_by_key.get(k)
        planned = _plan_record(docs, k, d.dest, d.fragment, old, claims, result, d.kept, d.bare)
        if planned is None or planned[0].refused:
            if old is not None:
                new_records.append(old)  # still owned; reconciled once the conflict clears
            continue
        plan, stale = planned
        _commit_record(docs, k, d.dest, old, plan, stale, claims, result, d.kept, d.bare)
        where = f"merge {d.dest.file}:{d.dest.pointer} ({', '.join(d.keys)})"
        if plan.created:
            result.created.append(where)
        elif plan.updated:
            result.updated.append(where)
        new_records.append(
            InstalledMerge(
                component=d.component,
                target=d.target,
                file=d.dest.file,
                pointer=d.dest.pointer,
                keys=list(plan.entries),
                entries=plan.entries,
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
    lock: Lock | None = None,
    trust_callback: TrustCallback | None = None,
) -> bool:
    """Run generators (gated by per-source consent). Returns True if trust was newly granted."""
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

    trust_changed = False
    have = {g.component for g in manifest.generated}
    for ref, d in desired_by_ref.items():
        already = ref in have and gen_inst.produces_present(root, d.spec)
        if already and not update:
            continue  # idempotent: outputs present and tracked
        # Consent gate: a source whose component runs code at install must be trusted —
        # via a persisted `agentry trust` decision (SHA-pinned in the lock), the interactive
        # prompt, or the one-shot `--allow-run` blanket override.
        source = ref.split("/", 1)[0]
        entry = lock.entry(source) if lock is not None else None
        if not allow_run and not (entry and entry.trusted):
            sha = entry.resolved if entry else "?"
            if trust_callback is not None and trust_callback(source, sha):
                if entry is not None:
                    entry.trusted = True
                    trust_changed = True
            else:
                cmds = "; ".join(gen_inst.describe(d.spec))
                result.warnings.append(
                    f"{ref}: generator skipped — source '{source}' is not trusted to run code. "
                    f"Run `{prog()} trust {source}` (or `{prog()} sync --allow-run`) "
                    f"to allow: {cmds}"
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
    return trust_changed


def _reconcile_link_merges(
    root: Path,
    docs: merge_inst.Documents,
    desired: list[DesiredLinkMerge],
    manifest: Manifest,
    result: SyncResult,
    claims: _Claims,
    kept_for: Callable[[Who], frozenset[str]],
) -> None:
    desired_by_key = {(d.component, d.target): d for d in desired}
    old_by_key = {(m.component, m.target): m for m in manifest.link_merges}
    orphan_bare = _orphan_bare(docs, [m for k, m in old_by_key.items() if k not in desired_by_key])

    # Remove orphans: the owned merge entries first, then the symlink. If the config can't
    # be parsed, the pair (link and record) is kept so the whole removal is retried later.
    new_records: list[InstalledLinkMerge] = []
    for k, m in old_by_key.items():
        if k in desired_by_key:
            continue
        merge_removed = _remove_record(
            docs, m, result, kept_for(k), frozenset(orphan_bare.get(m.component, set()))
        )
        if merge_removed is None:
            new_records.append(m)
            continue
        claims.release(k)
        if link_inst.remove_link(root, m.link_path) or merge_removed:
            result.removed.append(f"link+merge {m.link_path} + {m.file}:{m.pointer}")

    for k, d in desired_by_key.items():
        old = old_by_key.get(k)
        # Plan the merge half before touching the link: link+merge is atomic as a pair, so
        # a refused or unparseable config never leaves a linked script with no hooks.
        planned = _plan_record(docs, k, d.dest, d.fragment, old, claims, result, d.kept, d.bare)
        if planned is None or planned[0].refused:
            if old is not None:
                new_records.append(old)
            continue
        plan, stale = planned
        try:
            link_status = link_inst.install_link(root, d.artifact, d.link_path)
        except FileExistsError as exc:
            result.warnings.append(str(exc))
            if old is not None:
                new_records.append(old)  # old link and record stay intact together
            continue
        # The dest template (e.g. {repo}@{ref}) may have moved the link — drop the old one,
        # but only now that the new link exists, so a failed install never loses both.
        if old is not None and old.link_path != d.link_path:
            link_inst.remove_link(root, old.link_path)
        _commit_record(docs, k, d.dest, old, plan, stale, claims, result, d.kept, d.bare)
        if link_status == "created" or plan.created:
            result.created.append(f"link+merge {d.link_path} + {d.dest.file}:{d.dest.pointer}")
        elif link_status == "updated" or plan.updated:
            result.updated.append(f"link+merge {d.link_path}")
        new_records.append(
            InstalledLinkMerge(
                component=d.component,
                target=d.target,
                link_path=d.link_path,
                file=d.dest.file,
                pointer=d.dest.pointer,
                keys=list(plan.entries),
                entries=plan.entries,
            )
        )
    manifest.link_merges = new_records


# -- status (read-only drift report) -------------------------------------


@dataclass
class StatusRow:
    ref: str
    target: str
    where: str
    state: str  # ok | ok (kept) | missing | drift
    # Why the row is not ok, when re-running sync won't fix it (a refused merge, a
    # hand-edited entry). Doctor uses it instead of the generic "run sync" hint.
    note: str | None = None
    # Merge rows only: each entry agentry can't vouch for (edited, or unproven from a v1
    # manifest), so doctor can name them one per line.
    drifted: list[DriftedEntry] = field(default_factory=list)
    #: Variant rows only: the canonical ref this per-harness variant replaces on the target.
    replaces: str | None = None


@dataclass(frozen=True)
class DriftedEntry:
    """One merged entry whose on-disk value agentry didn't (provably) write."""

    file: str
    pointer: str
    key: str
    unproven: bool  # True: recorded by an older agentry; False: edited by hand
    keep_arg: str = ""  # the `agentry keep` argument: "<target>:<key>" or the bare key


def status(root: Path) -> tuple[list[StatusRow], list[str]]:
    store = ConfigStore.load(root)
    config = store.parsed()
    check_targets(config)  # same fail-fast as sync: a table missing a typo'd target misleads
    warnings: list[str] = []
    graph, _ = deps.resolve_graph(root, config, load_lock(root))
    warnings.extend(graph.warnings)
    augmented = config.model_copy(update={"sources": graph.sources, "components": graph.components})
    plan = compute_desired(root, augmented, warnings)
    links, copies, merges = plan.links, plan.copies, plan.merges
    generates, link_merges, transforms = plan.generates, plan.link_merges, plan.transforms

    rows: list[StatusRow] = []
    for dl in links:
        rows.append(
            StatusRow(
                dl.component, dl.target, dl.path, link_inst.link_state(root, dl.artifact, dl.path)
            )
        )
    for dc in copies:
        rows.append(
            StatusRow(
                dc.component, dc.target, dc.path, copy_inst.copy_state(root, dc.artifact, dc.path)
            )
        )

    # Merge rows compare fingerprints against the manifest's ownership records. Legacy v1
    # records are migrated in memory only — status never writes.
    manifest = load_manifest(root)
    docs = merge_inst.Documents(root)
    records: list[InstalledMerge | InstalledLinkMerge] = [*manifest.merges, *manifest.link_merges]
    _migrate_v1(
        docs,
        records,
        _desired_fragments(merges, link_merges),
        _source_fragments(root, augmented, config),
    )
    claims = _Claims(records)
    old_records = {(r.component, r.target): r for r in records}
    _assign_bare(merges, link_merges, docs, records)

    def merge_state(
        who: Who, dest: MergeDest, fragment: dict, kept: frozenset[str], bare: frozenset[str]
    ) -> tuple[str, str | None, list[DriftedEntry]]:
        try:
            doc = docs.get(dest.file)
        except merge_inst.MergeTargetError as exc:
            if str(exc) not in warnings:  # an unparseable config is reported once per file
                warnings.append(str(exc))
            return "missing", str(exc), []
        rec = old_records.get(who)
        same = rec is not None and (rec.file, rec.pointer) == (dest.file, dest.pointer)
        owned = dict(rec.entries) if rec is not None and same else {}
        ctx = _ctx(who, dest.file, dest.pointer, bare)
        state, note = merge_inst.named_state(
            doc,
            ctx,
            fragment,
            owned,
            claims.lookup(who, dest.file, dest.pointer),
            kept,
            installed=same,
        )
        drifted = [
            DriftedEntry(dest.file, dest.pointer, key, unproven, ctx.keep_arg(key))
            for key, unproven in merge_inst.drifted_keys(doc, dest.pointer, fragment, owned, kept)
        ]
        return state, note, drifted

    replaces = {(var, t): canon for (canon, t), var in plan.replaced.items()}
    for dm in merges:
        state, note, drifted = merge_state(
            (dm.component, dm.target), dm.dest, dm.fragment, dm.kept, dm.bare
        )
        rows.append(
            StatusRow(
                dm.component,
                dm.target,
                f"{dm.dest.file}:{dm.dest.pointer}",
                state,
                note,
                drifted,
                replaces.get((dm.component, dm.target)),
            )
        )
    for dg in generates:
        where = ", ".join(dg.spec.produces)
        state = "ok" if gen_inst.produces_present(root, dg.spec) else "missing"
        rows.append(StatusRow(dg.component, dg.target, where, state))
    for dlm in link_merges:
        link_ok = link_inst.link_state(root, dlm.artifact, dlm.link_path) == "ok"
        mstate, note, drifted = merge_state(
            (dlm.component, dlm.target), dlm.dest, dlm.fragment, dlm.kept, dlm.bare
        )
        if mstate == "drift" and link_ok:
            state = "drift"
        elif link_ok and mstate in ("ok", "ok (kept)"):
            state = mstate
        else:
            state = "missing"
        rows.append(
            StatusRow(
                dlm.component,
                dlm.target,
                f"{dlm.link_path} + {dlm.dest.file}:{dlm.dest.pointer}",
                state,
                note,
                drifted,
                replaces.get((dlm.component, dlm.target)),
            )
        )
    for dt in transforms:
        rows.append(
            StatusRow(
                dt.component, dt.target, dt.path, transform_inst.transform_state(root, dt.path)
            )
        )
    return rows, warnings
