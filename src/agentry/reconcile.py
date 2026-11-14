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

from . import deps, discovery, envscan, identity, plugin_manifest
from .config import ConfigStore
from .drivers import BUILTIN_DRIVERS, Driver, resolve_drivers
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
    #: "target '<t>' is not in the project's targets — skipped" warnings (also in the
    #: caller's warnings): a component listing a valid target the project hasn't activated.
    inactive: list[str] = field(default_factory=list)
    #: Contested named merge keys ``(file, pointer, key) -> (winner ref, transitive?)`` —
    #: the earliest claimant in config order (ADR-001 Rev 6). Losers get W5 at planning.
    winners: dict[tuple[str, str, str], tuple[Who, bool]] = field(default_factory=dict)
    #: Path claims lost in config order, dropped from the plan: ``(loser, target, path,
    #: winner ref, note)`` — status shows them as missing with the note.
    path_losers: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    #: plugin.json findings (also in the caller's warnings): rejected paths, name clashes,
    #: unsupported keys, ``${CLAUDE_PLUGIN_DATA}`` references — doctor reports them too.
    plugin_notes: list[str] = field(default_factory=list)
    #: The unsupported-key subset of ``plugin_notes``, kept apart so the CLI can summarize.
    plugin_unsupported: list[str] = field(default_factory=list)
    #: Name units lost in claim order (ADR-003): ``(loser, loser path, winner, unit, mode)``
    #: — the transfer line when the loser was the one installed.
    name_transfers: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    #: Agent identity findings (also in the caller's warnings): a missing frontmatter
    #: ``name``, or a ``:`` in it (ADR-003) — doctor reports them too.
    identity_notes: list[str] = field(default_factory=list)


def compute_desired(
    root: Path,
    config: Config,
    warnings: list[str],
    transitive: frozenset[str] | set[str] = frozenset(),
) -> DesiredPlan:
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
    plan = DesiredPlan()
    indexes: dict[str, dict[tuple[ComponentType, str], Path]] = {}
    for indexed in config.sources:
        sp = effective_root(root, indexed)
        if sp.exists():
            indexes[indexed.name] = discovery.index(sp)
            notes, unsupported = discovery.plugin_source_warnings(sp, indexed.name)
            plan.plugin_notes.extend(notes)
            plan.plugin_unsupported.extend(unsupported)
    warnings.extend(plan.plugin_notes)

    variants = _enabled_variants(config)

    for comp in config.components:
        if not comp.enabled:
            continue
        src = config.source(comp.source)
        if src is None:
            warnings.append(f"{comp.ref}: unknown source '{comp.source}'")
            continue
        _warn_inactive_targets(comp, config, drivers, plan, warnings)
        if comp.generate is not None:
            if not comp.applies_to(config.targets):
                continue  # no overlap with the project: the generator must not run
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
            _warn_placeholders(comp, artifact, plan, warnings)

        for tname in comp.applies_to(config.targets):
            driver = drivers.get(tname)
            if driver is None:
                continue  # already warned via unresolved_targets
            _desired_for_target(comp, src, tname, driver, artifact, plan, warnings, variants)

    _warn_unmatched_keep(config, plan, warnings)
    _resolve_claims(config, plan, warnings, frozenset(transitive), root=root, drivers=drivers)
    return plan


def _resolve_claims(
    config: Config,
    plan: DesiredPlan,
    warnings: list[str],
    transitive: frozenset[str],
    *,
    root: Path | None = None,
    drivers: dict[str, Driver] | None = None,
) -> None:
    """Settle duplicate claims in config order (ADR-001 Rev 6) — a pure function of config.

    Claim order is the order of ``config.components`` (declared, then transitive in BFS
    order). Paths (links, copies, transforms, link+merge links), compared case-insensitively
    since macOS/Windows filesystems are: the earliest component keeps the path, later ones
    are dropped (W5-path; a link+merge loses as a pair). Named merge keys contested by two
    components (unless every claim is a list — those are shared per element): the earliest
    wins, recorded in ``plan.winners`` for planning to refuse the others.
    """
    order = {c.ref: i for i, c in enumerate(config.components)}

    def rank(ref: str) -> int:
        return order.get(ref, len(order))

    _resolve_path_claims(plan, warnings, transitive, rank)
    if root is not None and drivers is not None:
        _resolve_name_claims(root, plan, drivers, warnings, transitive, rank)
    _resolve_key_claims(plan, transitive, rank)


@dataclass(frozen=True)
class _Named:
    """A path install whose target identifies its type by name (ADR-003)."""

    rank: int
    ref: str
    target: str
    path: str
    name: str


def _identity_name(
    ref: str, target: str, artifact: Path, warnings: list[str], notes: list[str]
) -> str:
    """An agent's identity on ``target``: its frontmatter ``name``, else its file stem."""
    name = identity.frontmatter_name(artifact)
    if name is None:
        name = artifact.stem
        msg = (
            f"{ref}: agent has no 'name' in its frontmatter, which {target} uses to identify "
            f"agents — treated it as '{name}' (its file name). Add 'name: {name}' to the agent."
        )
        if msg not in warnings:
            warnings.append(msg)
        if msg not in notes:
            notes.append(msg)
    elif ":" in name:
        msg = (
            f"{ref}: agent name '{name}' contains ':', which {target} reserves for plugin "
            "agents — installed as-is. Rename it in the agent's frontmatter."
        )
        if msg not in warnings:
            warnings.append(msg)
        if msg not in notes:
            notes.append(msg)
    return name


def _named_installs(
    plan: DesiredPlan,
    drivers: dict[str, Driver],
    warnings: list[str],
    rank: Callable[[str], int],
) -> list[_Named]:
    out: list[_Named] = []
    items: list[DesiredLink | DesiredCopy | DesiredTransform] = [
        *plan.links,
        *plan.copies,
        *plan.transforms,
    ]
    for d in items:
        driver = drivers.get(d.target)
        ctype = ComponentType(d.component.split("/")[1])
        if driver is None or driver.spec.identity.get(ctype) != identity.FRONTMATTER_NAME:
            continue
        if not d.artifact.is_file():
            continue
        name = _identity_name(d.component, d.target, d.artifact, warnings, plan.identity_notes)
        out.append(_Named(rank(d.component), d.component, d.target, d.path, name))
    return out


def _user_names(
    root: Path, drivers: dict[str, Driver], named: list[_Named]
) -> dict[str, dict[str, str]]:
    """Per target: ``casefold(name) -> path`` of agents agentry doesn't manage (W1-name).

    Only the project's own agent dirs are read (never ``~/.claude``), frontmatter only.
    """
    manifest = load_manifest(root)
    skip = {
        p.casefold()
        for p in [
            *(r.path for r in manifest.links),
            *(r.path for r in manifest.copies),
            *(r.path for r in manifest.transforms),
            *(n.path for n in named),
        ]
    }
    out: dict[str, dict[str, str]] = {}
    for target in sorted({n.target for n in named}):
        driver = drivers[target]
        dirs: list[str] = []
        builtin = BUILTIN_DRIVERS.get(target)
        for spec in (builtin.spec if builtin else None, driver.spec):
            if spec is None:
                continue
            for table in (spec.link, spec.copy):
                template = table.get(ComponentType.AGENT)
                found = identity.scan_dir(template) if template else None
                if found is not None:
                    dirs.append(found)
        out[target] = identity.scan_names(root, dirs, skip)
    return out


def _resolve_name_claims(
    root: Path,
    plan: DesiredPlan,
    drivers: dict[str, Driver],
    warnings: list[str],
    transitive: frozenset[str],
    rank: Callable[[str], int],
) -> None:
    """Name units (ADR-003): a target that identifies a type by frontmatter ``name`` sees
    two installs with one name as one claim, wherever their paths are. The user's own agent
    with that name wins (W1-name); otherwise the earliest in claim order (W5-name)."""
    named = _named_installs(plan, drivers, warnings, rank)
    if not named:
        return
    users = _user_names(root, drivers, named)
    losers: set[tuple[str, str]] = set()  # (component, casefolded path)
    by_unit: dict[tuple[str, str], list[_Named]] = {}
    for n in named:
        mine = users.get(n.target, {}).get(n.name.casefold())
        if mine is not None:
            msg = (
                f"{n.ref}: agent name '{n.name}' on {n.target} is already used by your agent "
                f"{mine} — skipped ({n.target} loads only one agent per name). Rename one of "
                f"them, or remove yours to install {n.ref}'s."
            )
            if msg not in warnings:
                warnings.append(msg)
            losers.add((n.ref, n.path.casefold()))
            note = f"agent name '{n.name}' is used by your agent {mine}"
            plan.path_losers.append((n.ref, n.target, n.path, "", note))
            continue
        by_unit.setdefault((n.target, n.name.casefold()), []).append(n)
    for (target, _), claims in by_unit.items():
        winner = min(claims, key=lambda c: (c.rank, c.ref))
        for n in claims:
            if n.ref == winner.ref:
                continue
            mode = _transitive_mode(winner.ref, n.ref, transitive)
            msg, note = _w5_name(n, winner.ref, mode)
            if msg not in warnings:
                warnings.append(msg)
            losers.add((n.ref, n.path.casefold()))
            plan.path_losers.append((n.ref, target, n.path, winner.ref, note))
            unit = f"agent name '{n.name}' on {target}"
            plan.name_transfers.append((n.ref, n.path, winner.ref, unit, mode))
    if losers:
        plan.links = [d for d in plan.links if (d.component, d.path.casefold()) not in losers]
        plan.copies = [d for d in plan.copies if (d.component, d.path.casefold()) not in losers]
        plan.transforms = [
            d for d in plan.transforms if (d.component, d.path.casefold()) not in losers
        ]


def _w5_name(n: _Named, winner: str, mode: str) -> tuple[str, str]:
    """(warning, status note) for an agent name an earlier claimant has (W5-name)."""
    head = f"{n.ref}: agent name '{n.name}' on {n.target} is already used by {winner}"
    why = f"({n.target} loads only one agent per name)"
    note = f"agent name '{n.name}' is used by {winner}"
    if mode == "loser":
        return (
            f"{head} — skipped {why}. Declare {n.ref} in .agentry.yml above {winner}, or "
            f"disable {winner}.",
            note,
        )
    if mode:
        return (
            f"{head} — skipped {why}. To change that, declare {n.ref} in .agentry.yml to give "
            "it priority.",
            note,
        )
    return (
        f"{head}, listed earlier in .agentry.yml — skipped {why}. Disable one of them, or "
        "reorder components to choose which wins.",
        f"{note} (listed earlier in .agentry.yml)",
    )


def _resolve_path_claims(
    plan: DesiredPlan,
    warnings: list[str],
    transitive: frozenset[str],
    rank: Callable[[str], int],
) -> None:
    """Contested install paths: the earliest claimant keeps the path; later ones are dropped
    from the plan with W5-path (a link+merge loses as a pair)."""
    # -- paths
    claims: dict[str, list[tuple[int, str, str, str]]] = {}
    path_items: list[DesiredLink | DesiredCopy | DesiredTransform] = [
        *plan.links,
        *plan.copies,
        *plan.transforms,
    ]
    for d in path_items:
        claims.setdefault(d.path.casefold(), []).append(
            (rank(d.component), d.component, d.target, d.path)
        )
    for dlm in plan.link_merges:
        claims.setdefault(dlm.link_path.casefold(), []).append(
            (rank(dlm.component), dlm.component, dlm.target, dlm.link_path)
        )
    losers: set[tuple[str, str]] = set()  # (component, casefolded path)
    for folded, cl in claims.items():
        refs = sorted({(r, ref) for r, ref, _, _ in cl})
        if len(refs) < 2:
            continue
        winner = refs[0][1]
        for _, ref, target, path in cl:
            if ref == winner:
                continue
            losers.add((ref, folded))
            mode = _transitive_mode(winner, ref, transitive)
            if mode == "loser":
                msg = (
                    f"{ref}: {path} is already installed by {winner} — skipped on {target}. "
                    f"Declare {ref} in .agentry.yml above {winner}, or disable {winner}."
                )
                note = f"{path} is provided by {winner}"
            elif mode:
                msg = (
                    f"{ref}: {path} is already installed by {winner} — skipped on {target}. To "
                    f"change that, declare {ref} in .agentry.yml to give it priority."
                )
                note = f"{path} is provided by {winner}"
            else:
                msg = (
                    f"{ref}: {path} is already installed by {winner}, listed earlier in "
                    f".agentry.yml — skipped on {target}. Disable one of them, or reorder "
                    "components to choose which wins."
                )
                note = f"{path} is provided by {winner} (listed earlier in .agentry.yml)"
            if msg not in warnings:
                warnings.append(msg)
            plan.path_losers.append((ref, target, path, winner, note))
    if losers:
        plan.links = [d for d in plan.links if (d.component, d.path.casefold()) not in losers]
        plan.copies = [d for d in plan.copies if (d.component, d.path.casefold()) not in losers]
        plan.transforms = [
            d for d in plan.transforms if (d.component, d.path.casefold()) not in losers
        ]
        plan.link_merges = [  # a link+merge loses as a pair: no merge without its link
            d for d in plan.link_merges if (d.component, d.link_path.casefold()) not in losers
        ]


def _resolve_key_claims(
    plan: DesiredPlan, transitive: frozenset[str], rank: Callable[[str], int]
) -> None:
    """Contested named merge keys (not all-list): the earliest claimant wins (``winners``)."""
    # -- named merge keys
    keyed: dict[tuple[str, str, str], list[tuple[int, Who, bool]]] = {}
    for dm in plan.merges:
        for key, value in dm.fragment.items():
            keyed.setdefault((dm.dest.file, dm.dest.pointer, key), []).append(
                (rank(dm.component), (dm.component, dm.target), isinstance(value, list))
            )
    for dlm in plan.link_merges:
        for key, value in dlm.fragment.items():
            keyed.setdefault((dlm.dest.file, dlm.dest.pointer, key), []).append(
                (rank(dlm.component), (dlm.component, dlm.target), isinstance(value, list))
            )
    for unit, cl in keyed.items():
        refs = {who[0] for _, who, _ in cl}  # one component on two targets isn't a contest
        if len(refs) < 2 or all(is_list for _, _, is_list in cl):
            continue  # uncontested, or every claim is an array (shared per element, Rev 5)
        _, winner, _ = min(cl, key=lambda c: (c[0], c[1]))
        plan.winners[unit] = (winner, winner[0] in transitive)


def _warn_placeholders(
    comp: Component, artifact: Path, plan: DesiredPlan, warnings: list[str]
) -> None:
    """Warn about placeholders in what a hook/MCP component installs that won't resolve.

    Only the entries agentry installs are scanned (for an inline plugin component, the
    ``hooks`` / ``mcpServers`` subtree of plugin.json, not its other fields).
    """
    try:
        text = plugin_manifest.merge_text(artifact, comp.type)
    except (ValueError, OSError):
        return  # unreadable or malformed: the merge step reports it
    for var in envscan.unset_env_refs(text):
        if var == "CLAUDE_PLUGIN_DATA":
            continue  # its own, more specific warning below
        warnings.append(
            f"{comp.ref}: references ${{{var}}}, which is unset — set it before your "
            "agent runs (agentry ships the placeholder; the runtime resolves it)"
        )
    for note in plugin_manifest.plugin_data_refs(comp.ref, comp.type, artifact):
        warnings.append(note)
        plan.plugin_notes.append(note)


def _warn_inactive_targets(
    comp: Component,
    config: Config,
    drivers: dict[str, Driver],
    plan: DesiredPlan,
    warnings: list[str],
) -> None:
    """One warning per valid component target the project hasn't activated (it's skipped).

    Unresolvable names are T17's fatal check, not this warning.
    """
    for t in comp.inactive_targets(config.targets):
        if t not in drivers:
            continue
        msg = (
            f"{comp.ref}: target '{t}' is not in the project's targets — skipped. Add it with "
            f"`{prog()} target use {t}`, or remove it from this component."
        )
        warnings.append(msg)
        plan.inactive.append(msg)


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
            entries, notes = _fragment_entries(artifact, comp.type, dest)
        except (ValueError, OSError) as exc:
            warnings.append(f"{comp.ref}: {exc}")
            return
        warnings.extend(f"{comp.ref}: {note}" for note in notes)
        # Defense-in-depth: drop hook events the target doesn't recognize (e.g. Claude
        # Code rejects a settings.json carrying unknown events). No-op for agents
        # without a hook-event policy.
        if comp.type is ComponentType.HOOK:
            entries = _hook_entries(comp, tname, driver, artifact, dest, entries, plan, warnings)
            if not entries:
                return
        _check_format(comp, tname, driver, plan, warnings, entries=entries)
        # Seam: a driver.transform (currently always None) would reshape `entries`
        # here for agents needing semantic translation (e.g. JSON→TOML) before merge.
        plan.merges.append(
            DesiredMerge(comp.ref, tname, dest, entries, list(entries), comp.kept_keys(tname))
        )


def _fragment_entries(
    artifact: Path, ctype: ComponentType, dest: MergeDest
) -> tuple[dict, list[str]]:
    """The named entries a plain-merge artifact provides, plus notes on what was left out.

    A plugin.json (an inline plugin component) yields only its hooks/mcpServers value; any
    other fragment file is unwrapped once by its section name.
    """
    if plugin_manifest.is_manifest(artifact):
        return plugin_manifest.manifest_entries(artifact, ctype)
    return merge_inst.select_entries(merge_inst.load_fragment(artifact), dest), []


def _hook_entries(
    comp: Component,
    tname: str,
    driver: Driver,
    artifact: Path,
    dest: MergeDest,
    entries: dict,
    plan: DesiredPlan,
    warnings: list[str],
) -> dict:
    """The hook events of ``entries`` worth merging on ``tname``, warning about the rest."""
    # Defense-in-depth: drop hook events the target doesn't recognize (e.g. Claude
    # Code rejects a settings.json carrying unknown events). No-op for agents
    # without a hook-event policy.
    entries, dropped = driver.filter_hook_events(entries)
    for bad in dropped:
        warnings.append(
            f"{comp.ref}: hook event '{bad}' is not a recognized {tname} event — skipped"
        )
    if plugin_manifest.is_manifest(artifact):
        return _drop_plugin_root_events(comp, tname, entries, plan, warnings)
    # A plugin-style hook ships ${...PLUGIN_ROOT} paths that only resolve inside a real
    # installed plugin. Merged verbatim into a config file they fail at startup; the fix
    # is a link+merge profile (which rewrites them). Warn rather than silently install a
    # dead hook.
    for cmd in link_merge_inst.plugin_root_refs(entries):
        warnings.append(
            f"{comp.ref}: hook command references a plugin-root variable "
            f"({cmd!r}), which only resolves inside an installed plugin — merged "
            f"into {dest.file} it will fail at startup. Configure a 'link+merge' "
            f"hook profile under target_profiles for this repo (see the "
            f"superpowers/arckit catalog entries)."
        )
    return entries


def _drop_plugin_root_events(
    comp: Component, tname: str, entries: dict, plan: DesiredPlan, warnings: list[str]
) -> dict:
    """Skip inline plugin.json hook events whose commands use ``${..._PLUGIN_ROOT}``.

    Such a path only resolves inside an installed Claude plugin, and an inline hook has no
    script directory agentry could link and rewrite it to (a link+merge profile needs one),
    so the event is left out rather than merged dead.
    """
    kept: dict = {}
    for event, groups in entries.items():
        refs = link_merge_inst.plugin_root_refs(groups)
        if not refs:
            kept[event] = groups
            continue
        note = (
            f"{comp.ref}: hook event '{event}' in {plugin_manifest.MANIFEST_PATH} runs "
            f"{refs[0]!r}, and agentry doesn't support ${{CLAUDE_PLUGIN_ROOT}} paths in inline "
            f"plugin hooks yet — skipped it on {tname}. Install the plugin in Claude Code "
            "itself to use this hook."
        )
        warnings.append(note)
        plan.plugin_notes.append(note)  # doctor reports it too
    return kept


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
    update_only: frozenset[str] | None = None,
) -> SyncResult:
    store = ConfigStore.load(root)
    config = store.parsed()
    # 0. Validate targets — a pure config check, before any network call or write: a typo'd
    #    target would otherwise resolve to nothing and uninstall the real one's components.
    check_targets(config)
    result = SyncResult()

    # 1. Resolve sources + the transitive dependency closure into the store.
    graph, lock = deps.resolve_graph(
        root, config, load_lock(root), update=update, frozen=frozen, update_only=update_only
    )
    result.resolved = graph.resolved
    result.warnings.extend(graph.warnings)
    save_lock(root, lock)

    # 2. Desired vs. installed. The augmented config carries synthesized sources and
    #    transitive components so reconcile treats them like any declared dependency.
    augmented = config.model_copy(update={"sources": graph.sources, "components": graph.components})
    plan = compute_desired(root, augmented, result.warnings, graph.transitive)
    result.summarized["unsupported"] = list(plan.skipped)
    result.summarized["format"] = list(plan.format_issues)
    result.summarized["inactive"] = list(plan.inactive)
    result.summarized["plugin"] = list(plan.plugin_unsupported)
    links, copies, merges = plan.links, plan.copies, plan.merges
    generates, link_merges, transforms = plan.generates, plan.link_merges, plan.transforms
    manifest = load_manifest(root)
    transform_cmd = config.transform.command if config.transform else []

    transitive = frozenset(graph.transitive)
    # Claim-order transfers (ADR-001 Rev 6 C2): reported once, in `updated` — but only after
    # the winner's install succeeded (checked below), never for one that hit a conflict.
    pending_transfers = _transfer_lines(plan, manifest, transitive)
    # Every path-strategy orphan is removed before any path is installed, so a path moving
    # between strategies (a loser's link → the winner's copy) never trips "not managed".
    _prune_links(root, links, manifest, result)
    _prune_copies(root, copies, manifest, result)
    _prune_transforms(root, transforms, manifest, result)
    _install_links(root, links, manifest, result)
    _install_copies(root, copies, manifest, result)
    trust_changed = _reconcile_generated(
        root,
        generates,
        manifest,
        result,
        allow_run=allow_run,
        update=update,
        lock=lock,
        trust_callback=trust_callback,
        update_only=update_only,
    )
    # One pass over every merge destination, after generators (ADR-001 §8).
    path_owned = {d.path.casefold() for d in links}
    path_owned |= {d.path.casefold() for d in copies}
    path_owned |= {d.path.casefold() for d in transforms}
    _reconcile_merge_targets(
        root,
        augmented,
        config,
        merges,
        link_merges,
        manifest,
        result,
        update=update,
        winners=plan.winners,
        path_owned=path_owned,
        transitive=transitive,
    )
    _reconcile_transforms(
        root, transforms, manifest, result, allow_transform=allow_transform, command=transform_cmd
    )

    installed_paths: set[tuple[str, str]] = set()
    for rec_path, comp in [
        *((r.path, r.component) for r in manifest.links),
        *((r.path, r.component) for r in manifest.copies),
        *((r.path, r.component) for r in manifest.transforms),
        *((r.link_path, r.component) for r in manifest.link_merges),
    ]:
        installed_paths.add((rec_path.casefold(), comp))
    for path, winner, line in pending_transfers:
        if (path.casefold(), winner) in installed_paths:
            result.updated.append(line)

    manifest.version = MANIFEST_VERSION
    save_manifest(root, manifest)
    if trust_changed:
        save_lock(root, lock)  # persist newly-granted per-source consent

    # 3. Housekeeping.
    result.gitignore_changed = ensure_gitignore(root)
    return result


def _prune_links(
    root: Path, desired: list[DesiredLink], manifest: Manifest, result: SyncResult
) -> None:
    desired_paths = {d.path for d in desired}
    kept: list[InstalledLink] = []
    for inst in manifest.links:
        if inst.path not in desired_paths:
            if link_inst.remove_link(root, inst.path):
                result.removed.append(f"link {inst.path}")
        else:
            kept.append(inst)
    manifest.links = kept


def _install_links(
    root: Path, desired: list[DesiredLink], manifest: Manifest, result: SyncResult
) -> None:
    desired_by_path = {d.path: d for d in desired}
    have = {inst.path: inst for inst in manifest.links}
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
        if path in have:
            have[path].component, have[path].target = d.component, d.target  # transfer label
        else:
            inst = InstalledLink(component=d.component, target=d.target, path=path)
            manifest.links.append(inst)
            have[path] = inst


def _prune_copies(
    root: Path, desired: list[DesiredCopy], manifest: Manifest, result: SyncResult
) -> None:
    desired_paths = {d.path for d in desired}
    kept: list[InstalledCopy] = []
    for inst in manifest.copies:
        if inst.path not in desired_paths:
            if copy_inst.remove_copy(root, inst.path):
                result.removed.append(f"copy {inst.path}")
        else:
            kept.append(inst)
    manifest.copies = kept


def _install_copies(
    root: Path, desired: list[DesiredCopy], manifest: Manifest, result: SyncResult
) -> None:
    desired_by_path = {d.path: d for d in desired}
    have = {inst.path: inst for inst in manifest.copies}
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
        if path in have:
            have[path].component, have[path].target = d.component, d.target  # transfer label
        else:
            inst = InstalledCopy(component=d.component, target=d.target, path=path)
            manifest.copies.append(inst)
            have[path] = inst


def _prune_transforms(
    root: Path, desired: list[DesiredTransform], manifest: Manifest, result: SyncResult
) -> None:
    desired_paths = {d.path for d in desired}
    kept: list[InstalledTransform] = []
    for inst in manifest.transforms:
        if inst.path not in desired_paths:
            if copy_inst.remove_copy(root, inst.path):  # a transformed file is a managed real file
                result.removed.append(f"transform {inst.path}")
        else:
            kept.append(inst)
    manifest.transforms = kept


def _transitive_mode(winner: str, loser: str, transitive: frozenset[str]) -> str:
    """How a claim-order contest involves transitive components (see ``Rival.transitive``)."""
    if winner in transitive:
        return "winner"
    return "loser" if loser in transitive else ""


def _transfer_lines(
    plan: DesiredPlan, manifest: Manifest, transitive: frozenset[str]
) -> list[tuple[str, str, str]]:
    """C2 candidates: ``(path, winner, line)`` per installed path whose owner changes to the
    claim-order winner. Emitted only once the winner's install has succeeded."""
    wanted: dict[str, str] = {}
    paths: list[tuple[str, str]] = [(d.path, d.component) for d in plan.links]
    paths += [(d.path, d.component) for d in plan.copies]
    paths += [(d.path, d.component) for d in plan.transforms]
    for path, component in paths:
        wanted.setdefault(path.casefold(), component)
    for dlm in plan.link_merges:
        wanted.setdefault(dlm.link_path.casefold(), dlm.component)
    lines: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    records: list[tuple[str, str]] = [
        *((r.path, r.component) for r in manifest.links),
        *((r.path, r.component) for r in manifest.copies),
        *((r.path, r.component) for r in manifest.transforms),
        *((r.link_path, r.component) for r in manifest.link_merges),
    ]
    for path, loser in records:
        winner = wanted.get(path.casefold())
        if winner is None or winner == loser or path.casefold() in seen:
            continue
        seen.add(path.casefold())
        lines.append(
            (
                path,
                winner,
                _transfer_line(path, winner, loser, _transitive_mode(winner, loser, transitive)),
            )
        )
    # Name units (ADR-003): the loser was installed at its own path; the winner's path differs.
    installed = {(path.casefold(), comp) for path, comp in records}
    winner_paths: dict[str, str] = {}
    for path, component in paths:
        winner_paths.setdefault(component, path)
    for loser, loser_path, winner, unit, mode in plan.name_transfers:
        win_path = winner_paths.get(winner)
        if win_path is None or (loser_path.casefold(), loser) not in installed:
            continue
        lines.append((win_path, winner, _transfer_line(unit, winner, loser, mode)))
    return lines


def _transfer_line(unit: str, winner: str, loser: str, mode: str) -> str:
    if mode == "loser":
        return (
            f"{unit} now comes from {winner} instead of {loser} — to keep {loser}'s version, "
            f"declare {loser} in .agentry.yml above {winner}, or disable {winner}"
        )
    if mode:
        return (
            f"{unit} now comes from {winner} instead of {loser} — to keep {loser}'s version, "
            f"declare {loser} in .agentry.yml to give it priority"
        )
    return (
        f"{unit} now comes from {winner} (listed earlier in .agentry.yml) instead of {loser} — "
        f"to keep {loser}'s version, list it before {winner} in .agentry.yml, or disable "
        f"{winner}"
    )


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
    _prune_transforms(root, desired, manifest, result)  # no-op after sync's prune phase
    records = {inst.path: inst for inst in manifest.transforms}
    have = set(records)
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
        if path in records:
            records[path].component, records[path].target = d.component, d.target
        else:
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
    """Who owns what in each merge destination, across every live record (ADR-001).

    Named mode: one owner per ``(file, pointer, key)`` — no component writes over, or
    adopts, an entry another live component installed (W5). Array mode (Rev 5 §5): a
    refcount per element, ``(file, pointer, key, fp) -> {records}``, counting *every* record
    that lists the fingerprint (pre-existing and kept ones too), so a shared element is
    removed only when its last claimant goes.
    """

    def __init__(self, records: list[InstalledMerge | InstalledLinkMerge]) -> None:
        self._by: dict[tuple[str, str, str], Who] = {}
        #: Contested named keys settled in config order (ADR-001 Rev 6), set by the caller.
        self.winners: dict[tuple[str, str, str], tuple[Who, bool]] = {}
        #: Transitive component refs (for the W5 / transfer hint wording).
        self.transitive: frozenset[str] = frozenset()
        #: Key transfers awaiting the winner's install: (winner, file, pointer, key, line).
        self.pending_transfers: list[tuple[Who, str, str, str, str]] = []
        self._elements: dict[tuple[str, str, str, str], set[Who]] = {}
        self._array_keys: dict[tuple[str, str, str], set[Who]] = {}
        for r in records:
            self.claim((r.component, r.target), r.file, r.pointer, r.entries)

    def claim(self, who: Who, file: str, pointer: str, entries: dict[str, OwnedEntry]) -> None:
        for key, entry in entries.items():
            if entry.mode == "array":
                self._array_keys.setdefault((file, pointer, key), set()).add(who)
                for fp in entry.items:
                    if fp is not None:
                        self._elements.setdefault((file, pointer, key, fp), set()).add(who)
            elif not entry.preexisting:
                self._by[(file, pointer, key)] = who

    def rival(self, who: Who, file: str, pointer: str) -> Callable[[str], merge_inst.Rival | None]:
        """The config-order winner of a contested key, when it isn't ``who`` (W5)."""

        def other(key: str) -> merge_inst.Rival | None:
            won = self.winners.get((file, pointer, key))
            if won is None or won[0][0] == who[0]:
                return None
            mode = "winner" if won[1] else ("loser" if who[0] in self.transitive else "")
            return merge_inst.Rival(won[0][0], mode)

        return other

    def release_key(self, who: Who, file: str, pointer: str, key: str) -> None:
        """Drop ``who``'s claim on one key (it lost the key in config order)."""
        if self._by.get((file, pointer, key)) == who:
            del self._by[(file, pointer, key)]
        self._array_keys.get((file, pointer, key), set()).discard(who)
        for (f, p, k, _), claimants in self._elements.items():
            if (f, p, k) == (file, pointer, key):
                claimants.discard(who)

    def release(self, who: Who) -> None:
        self._by = {k: v for k, v in self._by.items() if v != who}
        for claimants in [*self._elements.values(), *self._array_keys.values()]:
            claimants.discard(who)

    def lookup(self, who: Who, file: str, pointer: str) -> Callable[[str], str | None]:
        """Another live owner of ``key`` — named, or array-mode (a shape conflict). The same
        component through another target returns ``""``: it already wrote this very entry."""

        def other(key: str) -> str | None:
            owner = self._by.get((file, pointer, key))
            if owner is not None and owner != who:
                return "" if owner[0] == who[0] else owner[0]
            for w in sorted(self._array_keys.get((file, pointer, key), set())):
                if w[0] != who[0]:
                    return w[0]
            return None

        return other

    def shared(self, who: Who, file: str, pointer: str) -> Callable[[str, str], bool]:
        """Whether another live record also claims element ``fp`` of array key ``key``."""

        def other(key: str, fp: str) -> bool:
            return any(w != who for w in self._elements.get((file, pointer, key, fp), ()))

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


def _convert_list_records(
    docs: merge_inst.Documents,
    records: list[InstalledMerge | InstalledLinkMerge],
    desired: dict[Who, tuple[MergeDest, dict]],
    fallback: FragmentLookup | None = None,
) -> None:
    """Turn PR 1 / v1 named-mode records of list-valued keys into array mode (Rev 5 §8).

    In memory; sync persists the result. The fragment comes from the desired plan, or —
    for a record being removed this sync (disabled, uninstalled) — from ``fallback``, which
    re-reads the source; so removal is per element too, and only agentry's identical
    groups go. A record whose fragment can't be re-derived keeps its named form (whole-value
    removal, left in place if the user changed it).
    """
    for r in records:
        if not any(e.mode == "named" for e in r.entries.values()):
            continue
        want = desired.get((r.component, r.target))
        if want is not None and (want[0].file, want[0].pointer) == (r.file, r.pointer):
            fragment: dict | None = want[1]
        else:
            fragment = fallback(r) if fallback is not None else None
        if fragment is None or not any(
            e.mode == "named" and isinstance(fragment.get(k), list) for k, e in r.entries.items()
        ):
            continue
        try:
            doc = docs.get(r.file)
        except merge_inst.MergeTargetError:
            continue
        r.entries = merge_inst.convert_named_lists(doc, r.pointer, r.entries, fragment)


def _hand_over_created_keys(
    records: list[InstalledMerge | InstalledLinkMerge],
    desired: dict[Who, tuple[MergeDest, dict]],
) -> None:
    """Before planning: when the record that created an array key leaves it this sync
    (disabled, moved, or the key dropped from its fragment) while another record that stays
    still appends to that key, pass ``created_key`` on — so the last one out deletes the key
    it empties instead of leaving ``[]`` behind.

    Done up front on the old records (which planning reads), so it doesn't depend on the
    order components are processed in. Pre-existing/kept records never receive it.
    """
    leaving: list[tuple[InstalledMerge | InstalledLinkMerge, str]] = []
    staying: dict[tuple[str, str, str], list[OwnedEntry]] = {}
    for r in records:
        want = desired.get((r.component, r.target))
        same = want is not None and (want[0].file, want[0].pointer) == (r.file, r.pointer)
        for key, entry in r.entries.items():
            if entry.mode != "array":
                continue
            if same and want is not None and key in want[1]:
                if not entry.preexisting:
                    staying.setdefault((r.file, r.pointer, key), []).append(entry)
            elif entry.created_key:
                leaving.append((r, key))
    for r, key in leaving:
        heirs = staying.get((r.file, r.pointer, key))
        if heirs:
            heirs[0].created_key = True


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
            return _fragment_entries(artifact, ctype, MergeDest(rec.file, rec.pointer))[0]
        except (ValueError, OSError):
            return None

    return lookup


def _ctx(
    who: Who, file: str, pointer: str, bare: frozenset[str] = frozenset()
) -> merge_inst.MergeContext:
    return merge_inst.MergeContext(ref=who[0], target=who[1], file=file, pointer=pointer, bare=bare)


def _differs(rec: OwnedEntry | None, have: str, want: str) -> bool:
    """Whether a recorded entry is one the user now owns a different value of (W2/W1b)."""
    if rec is None or rec.mode == "array":
        return False
    if rec.preexisting:
        return have != want
    return rec.fingerprint != have


def _drifted(rec: OwnedEntry | None, have: object, want: object) -> bool:
    """Whether this key needs the user's decision on this target (drives keep hints)."""
    if rec is not None and rec.mode == "array":
        on_disk = {merge_inst.fingerprint(e) for e in have} if isinstance(have, list) else set()
        if any(f is not None and f not in on_disk for f in rec.items):
            return True  # a group of ours was edited or removed (W2)
        wanted = [merge_inst.fingerprint(e) for e in want] if isinstance(want, list) else []
        return any(  # the user's copy no longer matches upstream (array W1b)
            u is not None and u in on_disk and i < len(wanted) and wanted[i] != u
            for i, u in enumerate(rec.user_items)
        )
    return _differs(rec, merge_inst.fingerprint(have), merge_inst.fingerprint(want))


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
            if key in current and _drifted(owned.get(key), current[key], value):
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
            if e.preexisting or e.mode == "array":
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
    claims: _Claims | None = None,
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
        claims.shared((rec.component, rec.target), rec.file, rec.pointer) if claims else None,
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
        shared=claims.shared(who, dest.file, dest.pointer),
        rival=claims.rival(who, dest.file, dest.pointer),
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
        _remove_record(docs, old, result, kept, bare, claims)
    doc = docs.get(dest.file)
    ctx = _ctx(who, dest.file, dest.pointer, bare)
    if stale:
        _, warnings = merge_inst.remove_named(
            doc, ctx, stale, shared=claims.shared(who, dest.file, dest.pointer)
        )
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
    winners: dict[tuple[str, str, str], tuple[Who, bool]] | None = None,
    path_owned: set[str] | None = None,
    transitive: frozenset[str] = frozenset(),
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
    from_source = _source_fragments(root, config, declared)
    _migrate_v1(docs, records, _desired_fragments(merges, link_merges), from_source)
    desired_frags = _desired_fragments(merges, link_merges)
    _convert_list_records(docs, records, desired_frags, from_source)
    _hand_over_created_keys(records, desired_frags)
    claims = _Claims(records)
    claims.winners = dict(winners or {})
    claims.transitive = frozenset(transitive)
    _release_lost_keys(docs, records, claims, result)
    _assign_bare(merges, link_merges, docs, records)
    kept_for = _kept_lookup(config, declared)
    _reconcile_merges(docs, merges, manifest, result, claims, kept_for)
    _reconcile_link_merges(
        root, docs, link_merges, manifest, result, claims, kept_for, path_owned or set()
    )
    docs.flush()
    owned_now: set[tuple[Who, str, str, str]] = set()
    for mrec in manifest.merges:
        owned_now.update(
            ((mrec.component, mrec.target), mrec.file, mrec.pointer, k) for k in mrec.entries
        )
    for lrec in manifest.link_merges:
        owned_now.update(
            ((lrec.component, lrec.target), lrec.file, lrec.pointer, k) for k in lrec.entries
        )
    for winner, file, pointer, key, line in claims.pending_transfers:
        if (winner, file, pointer, key) in owned_now:  # the winner really took it over
            result.updated.append(line)


def _release_lost_keys(
    docs: merge_inst.Documents,
    records: list[InstalledMerge | InstalledLinkMerge],
    claims: _Claims,
    result: SyncResult,
) -> None:
    """Before planning: a component that owns a key another component now wins in config
    order gives it up — removed through the normal fingerprint check, so the winner installs
    into the free key in this same sync (ADR-001 Rev 6 (a)). A loser's entry the user edited
    stays (W4) and is no longer anyone's: the winner then refuses it (W1), never overwriting.
    """
    for r in records:
        who = (r.component, r.target)
        lost = {
            key: entry
            for key, entry in r.entries.items()
            if not entry.preexisting
            and (won := claims.winners.get((r.file, r.pointer, key))) is not None
            and won[0][0] != r.component
        }
        if not lost:
            continue
        try:
            doc = docs.get(r.file)
        except merge_inst.MergeTargetError:
            continue
        for key, entry in lost.items():
            ctx = _ctx(who, r.file, r.pointer)
            removed, warnings = merge_inst.remove_named(
                doc, ctx, {key: entry}, shared=claims.shared(who, r.file, r.pointer)
            )
            result.warnings.extend(warnings)
            winner, _ = claims.winners[(r.file, r.pointer, key)]
            if removed:
                mode = _transitive_mode(winner[0], r.component, claims.transitive)
                claims.pending_transfers.append(
                    (
                        winner,
                        r.file,
                        r.pointer,
                        key,
                        _transfer_line(f"{r.file}:{r.pointer}.{key}", winner[0], r.component, mode),
                    )
                )
            del r.entries[key]
            if key in r.keys:
                r.keys.remove(key)
            claims.release_key(who, r.file, r.pointer, key)


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
            docs, m, result, kept_for(k), frozenset(orphan_bare.get(m.component, set())), claims
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
    update_only: frozenset[str] | None = None,
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
        source = ref.split("/", 1)[0]
        updating = update and (update_only is None or source in update_only)
        if already and not updating:
            continue  # idempotent: outputs present and tracked (and not being updated)
        # Consent gate: a source whose component runs code at install must be trusted —
        # via a persisted `agentry trust` decision (SHA-pinned in the lock), the interactive
        # prompt, or the one-shot `--allow-run` blanket override.
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
    path_owned: set[str] | None = None,
) -> None:
    owned_paths = path_owned or set()
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
            docs, m, result, kept_for(k), frozenset(orphan_bare.get(m.component, set())), claims
        )
        if merge_removed is None:
            new_records.append(m)
            continue
        claims.release(k)
        # A path now owned by a link/copy/transform winner (ADR-001 Rev 6) isn't ours to drop.
        unlinked = m.link_path.casefold() not in owned_paths and link_inst.remove_link(
            root, m.link_path
        )
        if unlinked or merge_removed:
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


@dataclass(frozen=True)
class Planned:
    """One resolve + desired-plan computation, shared so a caller (``doctor``) and
    :func:`status` don't each resolve and plan the project.

    **Single-use for status:** :func:`status` mutates the plan's merges (it assigns their
    bare-key sets), so pass a ``Planned`` to it at most once, after reading what you need.
    """

    config: Config  #: the declared config, as loaded
    graph: deps.DepGraph
    augmented: Config  #: ``config`` plus the graph's synthesized sources and components
    plan: DesiredPlan
    warnings: tuple[str, ...]  #: graph warnings, then planning warnings


def plan_for_status(root: Path, config: Config) -> Planned:
    """Resolve ``config``'s dependency graph from the lock and compute the desired plan.

    Read-only. Raises :class:`~agentry.resolver.ResolveError` /
    :class:`~agentry.deps.DependencyError` like :func:`status`.
    """
    warnings: list[str] = []
    graph, _ = deps.resolve_graph(root, config, load_lock(root))
    warnings.extend(graph.warnings)
    augmented = config.model_copy(update={"sources": graph.sources, "components": graph.components})
    plan = compute_desired(root, augmented, warnings, graph.transitive)
    return Planned(config, graph, augmented, plan, tuple(warnings))


def status(root: Path, *, planned: Planned | None = None) -> tuple[list[StatusRow], list[str]]:
    """Per (component, target) install state, plus warnings. Read-only.

    ``planned`` reuses a resolve + plan the caller already made (see :class:`Planned`;
    single-use); without it, status resolves and plans itself.
    """
    config = planned.config if planned is not None else ConfigStore.load(root).parsed()
    check_targets(config)  # same fail-fast as sync: a table missing a typo'd target misleads
    if planned is None:
        planned = plan_for_status(root, config)
    warnings = list(planned.warnings)
    graph, augmented, plan = planned.graph, planned.augmented, planned.plan
    links, copies, merges = plan.links, plan.copies, plan.merges
    generates, link_merges, transforms = plan.generates, plan.link_merges, plan.transforms

    rows: list[StatusRow] = []
    # Paths a component lost to one listed earlier (ADR-001 Rev 6): missing, with the reason.
    for ref, target, path, _winner, why in plan.path_losers:
        rows.append(StatusRow(ref, target, path, "missing", why))
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
    from_source = _source_fragments(root, augmented, config)
    _migrate_v1(docs, records, _desired_fragments(merges, link_merges), from_source)
    _convert_list_records(docs, records, _desired_fragments(merges, link_merges), from_source)
    claims = _Claims(records)
    claims.winners = dict(plan.winners)
    claims.transitive = frozenset(graph.transitive)
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
            shared=claims.shared(who, dest.file, dest.pointer),
            rival=claims.rival(who, dest.file, dest.pointer),
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
