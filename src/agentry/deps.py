"""Transitive dependency resolution — build the closure of components to install.

A component declares what it needs via ``requires`` in its source's ``agentry.yaml``
descriptor (see :class:`~agentry.models.Dependency`). Starting from the *roots* (the
enabled components in ``.agentry.yml``), :func:`resolve_graph` walks the dependency
graph breadth-first:

1. Each dependency points at another component, in an existing configured source
   (``source``) or in an arbitrary git repo (``url``). A ``url`` dependency is pulled
   into a **synthesized** source — recorded in the lockfile only, never in ``.agentry.yml``.
2. Sources are downloaded into the store on demand so their own descriptors can be read,
   making resolution genuinely recursive (A → B → C).
3. Cycles are broken by a visited set keyed on the component ref.
4. **Version awareness** is strict: if two requirers pin the same repo (or named source)
   to different refs, resolution aborts with a :class:`DependencyError` naming both sides.

The result is a :class:`DepGraph` whose ``sources`` and ``components`` are the explicit
config entries *plus* the transitive closure. The reconcile engine treats that augmented
set exactly like a hand-written config, so installation is unchanged downstream.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import discovery
from .config import LOCK_NAME
from .lockfile import upsert_entry
from .models import Component, Config, Dependency, Lock, LockEntry, Source, SourceType
from .naming import repo_basename
from .progname import prog
from .resolver import ResolveError, effective_root, resolve


class DependencyError(RuntimeError):
    """A dependency could not be satisfied (e.g. a version conflict)."""


@dataclass(frozen=True)
class DepEdge:
    dependent: str  # component ref that declares the requirement
    dependency: str  # component ref it requires


@dataclass
class DepGraph:
    sources: list[Source] = field(default_factory=list)  # config sources + synthesized
    components: list[Component] = field(default_factory=list)  # enabled roots + transitive
    edges: list[DepEdge] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)  # source name -> resolved id
    transitive: set[str] = field(default_factory=set)  # refs pulled in transitively
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Node:
    comp: Component
    targets: tuple[str, ...]  # effective targets inherited by this node's dependencies


def _repo_basename(url: str) -> str:
    return repo_basename(url, fallback="dep")


def _synth_name(url: str, taken: set[str]) -> str:
    """A deterministic, collision-free logical name for a transitive git source."""
    base = _repo_basename(url)
    if base not in taken:
        return base
    suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{suffix}"


def pin_mismatch(entry: LockEntry, src: Source) -> str | None:
    """Why a lock pin no longer describes ``src`` (``None`` when it still does).

    Compares the source identity the pin was resolved from — type, and for git the url and
    requested ref — against the current config. Local sources are re-hashed on every
    resolve, so only a type switch invalidates them. A ``None`` ref in the lock (written by
    an older agentry) is treated as unknown rather than changed, keeping old locks
    idempotent.
    """
    if entry.type is not src.type:
        return f"type: {entry.type.value} → {src.type.value}"
    if src.type is not SourceType.GIT:
        return None
    if entry.url != src.url:
        return f"url: {entry.url} → {src.url}"
    if entry.ref is not None and entry.ref != src.ref:
        return f"ref: {entry.ref} → {src.ref}"
    return None


def _choose_pin(
    existing: LockEntry | None, src: Source, *, update: bool, frozen: bool
) -> str | None:
    """The locked revision to check out for ``src``, or ``None`` to resolve its ref afresh.

    A pin is only valid for the identity it was resolved from: an edited url/ref makes the
    locked SHA meaningless (it may not even exist in the new origin), so it is dropped.
    Under ``frozen`` a missing or stale pin is an error instead.
    """
    if existing is None:
        if frozen:
            raise ResolveError(
                f"--frozen: source '{src.name}' is not pinned in {LOCK_NAME}; "
                f"run `{prog()} update` to refresh the lockfile first"
            )
        return None
    changed = pin_mismatch(existing, src)
    if changed and frozen:
        raise ResolveError(
            f"--frozen: source '{src.name}' changed in .agentry.yml since it was locked "
            f"({changed}); run `{prog()} update` to re-pin"
        )
    return None if (update or changed) else existing.resolved


def _check_provided(
    root: Path,
    comp: Component,
    dep: Dependency,
    dep_src: Source,
    dep_ref: str,
    update_only: frozenset[str] | None,
    warnings: list[str],
) -> None:
    """Warn when ``dep_src`` doesn't provide the required component — or, in a scoped
    update that moved ``comp``'s source but not ``dep_src``, refuse before anything is
    written (the new revision needs a component the pinned dependency lacks)."""
    idx = discovery.index(effective_root(root, dep_src))
    if (dep.type, dep.name) in idx:
        return
    if update_only is not None and comp.source in update_only and dep_src.name not in update_only:
        raise DependencyError(
            f"{comp.ref} now requires {dep_ref}, but the pinned source '{dep_src.name}' "
            f"doesn't provide '{dep.name}'. Nothing was changed. Update both: "
            f"`{prog()} update {comp.source} {dep_src.name}`"
        )
    warnings.append(
        f"{comp.ref} requires {dep_ref}, but '{dep.name}' is not provided by "
        f"source '{dep_src.name}'"
    )


def _restore_checkouts(root: Path, moved: list[tuple[Source, str]], *, normalize: bool) -> None:
    """Put store clones back at their locked revisions after a failed resolve.

    Link installs are live symlinks into the store, so a source left checked out at a new
    revision would change what's installed even though the lock (never written) still pins
    the old one. The old SHA is already in the clone, so this is a local checkout. Best
    effort: the original error is what the user needs to see.
    """
    for src, sha in reversed(moved):
        if src.type is not SourceType.GIT:
            continue  # local sources are a symlink to the user's dir; nothing to restore
        with contextlib.suppress(ResolveError):
            resolve(root, src, pinned=sha, normalize=normalize)


def resolve_graph(
    root: Path,
    config: Config,
    lock: Lock,
    *,
    update: bool = False,
    frozen: bool = False,
    update_only: frozenset[str] | None = None,
) -> tuple[DepGraph, Lock]:
    """Resolve every source and the transitive component closure (see :func:`_resolve_graph`).

    If resolution fails part-way (a refused scoped update, an unreachable dependency), every
    source this run already checked out at a new revision is put back at its locked one
    before the error propagates — the lock is never written on failure, and link installs
    are live symlinks into the store, so the store must keep matching the lock.
    """
    moved: list[tuple[Source, str]] = []
    try:
        return _resolve_graph(
            root,
            config,
            lock,
            update=update,
            frozen=frozen,
            update_only=update_only,
            moved=moved,
        )
    except BaseException:
        # Any failure — a refused update, an unreachable dependency, or Ctrl-C mid-walk —
        # leaves the lock unwritten, so the store must go back to matching it.
        _restore_checkouts(root, moved, normalize=config.hashing.normalize_line_endings)
        raise


def _resolve_graph(
    root: Path,
    config: Config,
    lock: Lock,
    *,
    update: bool = False,
    frozen: bool = False,
    update_only: frozenset[str] | None = None,
    moved: list[tuple[Source, str]],
) -> tuple[DepGraph, Lock]:
    """Resolve every source and the transitive component closure.

    Returns the augmented :class:`DepGraph` and a freshly-built :class:`Lock` (config
    sources plus the synthesized sources actually reached). The caller decides whether to
    persist the lock.

    ``frozen`` (mutually exclusive with ``update``) installs strictly from ``.agentry.lock``:
    every source — configured *or* transitive — must already be pinned, and a source whose
    resolved value differs from the lock (a moved git ref, a changed local source) aborts
    with :class:`~agentry.resolver.ResolveError`. This is the reproducible-CI guarantee.
    """
    graph = DepGraph()
    new_lock = Lock(version=lock.version)
    cfg_names = {s.name for s in config.sources}
    sources_by_name: dict[str, Source] = {s.name: s for s in config.sources}
    # url -> (ref, owning source name); seeded from config so a dep url that matches a
    # configured source reuses it (and conflicts if the refs disagree).
    url_index: dict[str, tuple[str, str]] = {
        s.url: (s.ref, s.name) for s in config.sources if s.type is SourceType.GIT and s.url
    }

    def ensure_resolved(src: Source) -> None:
        if src.name in graph.resolved:
            return
        existing = lock.entry(src.name)
        # `update` re-resolves every source, or — with `update_only` (`agentry update A B`) —
        # just the named ones; every other source keeps its lock pin (and so its trust).
        updating = update and (update_only is None or src.name in update_only)
        pinned = _choose_pin(existing, src, update=updating, frozen=frozen)
        entry = resolve(root, src, pinned=pinned, normalize=config.hashing.normalize_line_endings)
        if frozen and existing is not None and entry.resolved != existing.resolved:
            raise ResolveError(
                f"--frozen: source '{src.name}' drifted from {LOCK_NAME} "
                f"({existing.resolved} != {entry.resolved})"
            )
        entry.synthesized = src.name not in cfg_names
        # Carry forward consent only while the source's origin and pinned SHA are unchanged —
        # a moved source must be re-trusted (the SHA-pinned consent contract). A new url
        # revokes trust even at the same SHA (a fork shares history but not its owner); a ref
        # change that lands on the same SHA keeps it, since trust is about content.
        entry.trusted = bool(
            existing
            and existing.trusted
            and existing.resolved == entry.resolved
            and existing.type is entry.type
            and existing.url == entry.url
        )
        upsert_entry(new_lock, entry)
        graph.resolved[src.name] = entry.resolved
        if existing is not None and entry.resolved != existing.resolved:
            moved.append((src, existing.resolved))

    # 1. Resolve every configured source up front. Failures here abort (as before).
    for s in config.sources:
        ensure_resolved(s)

    # 2. Walk the dependency closure from the enabled roots.
    roots: dict[str, Component] = {c.ref: c for c in config.components if c.enabled}
    trans: dict[str, tuple[Component, set[str]]] = {}
    work: deque[_Node] = deque(
        _Node(c, tuple(c.applies_to(config.targets))) for c in roots.values()
    )
    visited: set[str] = set()

    while work:
        node = work.popleft()
        comp = node.comp
        if comp.ref in visited:
            continue
        visited.add(comp.ref)

        src = sources_by_name.get(comp.source)
        if src is None or comp.source not in graph.resolved:
            continue  # unresolved/unknown source — compute_desired warns about the component

        for dep in discovery.requires_for(effective_root(root, src), comp.type, comp.name):
            dep_src = _resolve_dep_source(
                dep,
                comp.ref,
                comp.source,
                sources_by_name,
                url_index,
                ensure_resolved,
                graph.warnings,
            )
            if dep_src is None:
                continue
            dep_ref = f"{dep_src.name}/{dep.type.value}/{dep.name}"
            graph.edges.append(DepEdge(comp.ref, dep_ref))

            if dep_src.name in graph.resolved:
                _check_provided(root, comp, dep, dep_src, dep_ref, update_only, graph.warnings)

            if dep_ref in roots:
                continue  # already declared and managed explicitly

            child_targets = set(node.targets)
            if dep_ref in trans:
                trans[dep_ref][1].update(child_targets)
            else:
                dep_comp = Component(
                    source=dep_src.name,
                    type=dep.type,
                    name=dep.name,
                    enabled=True,
                    targets=sorted(child_targets),
                )
                trans[dep_ref] = (dep_comp, child_targets)
                work.append(_Node(dep_comp, tuple(sorted(child_targets))))
    # 3. Assemble the augmented graph.
    graph.components = list(roots.values())
    for ref, (comp, targets) in trans.items():
        graph.components.append(comp.model_copy(update={"targets": sorted(targets)}))
        graph.transitive.add(ref)
    graph.sources = list(sources_by_name.values())
    return graph, new_lock


def _resolve_dep_source(
    dep: Dependency,
    requester_ref: str,
    requester_source: str,
    sources_by_name: dict[str, Source],
    url_index: dict[str, tuple[str, str]],
    ensure_resolved: Callable[[Source], None],
    warnings: list[str],
) -> Source | None:
    """Map one dependency onto a (possibly synthesized) source, enforcing version policy."""
    if not dep.source and not dep.url:
        base = sources_by_name.get(requester_source)  # same source as the requirer
        if base is None:
            return None
        if dep.subdir and dep.subdir != base.subdir:
            # Sibling-path dependency: a component needs another component living at a
            # *different subdir of the same repo* (the common monorepo layout). Resolve it
            # as a sibling view of the requester's source rooted at dep.subdir.
            return _sibling_source(base, dep.subdir, sources_by_name, ensure_resolved, warnings)
        return base

    if dep.source:
        src = sources_by_name.get(dep.source)
        if src is None:
            warnings.append(
                f"{requester_ref} requires source '{dep.source}', which is not configured"
            )
            return None
        if dep.ref and src.type is SourceType.GIT and src.ref != dep.ref:
            raise DependencyError(
                f"version conflict on source '{dep.source}': configured ref '{src.ref}', "
                f"but {requester_ref} requires ref '{dep.ref}'"
            )
        if dep.subdir and dep.subdir != src.subdir:
            return _sibling_source(src, dep.subdir, sources_by_name, ensure_resolved, warnings)
        return src

    # url-based — transitive, recorded in the lock only.
    url = dep.url
    if url is None:  # Dependency's validator guarantees url when source is unset
        warnings.append(f"{requester_ref} has a dependency with neither 'source' nor 'url'")
        return None
    ref = dep.ref or "main"
    if url in url_index:
        existing_ref, owner = url_index[url]
        if existing_ref != ref:
            raise DependencyError(
                f"version conflict on {url}: '{owner}' pins ref '{existing_ref}', "
                f"but {requester_ref} requires ref '{ref}'"
            )
        src = sources_by_name[owner]
        ensure_resolved(src)
        return src

    name = _synth_name(url, set(sources_by_name))
    src = Source(name=name, type=SourceType.GIT, url=url, ref=ref, subdir=dep.subdir)
    sources_by_name[name] = src
    url_index[url] = (ref, name)
    try:
        ensure_resolved(src)
    except ResolveError as exc:
        warnings.append(f"{requester_ref} dependency {url}@{ref}: {exc}")
        return None
    return src


def _sibling_source(
    base: Source,
    subdir: str,
    sources_by_name: dict[str, Source],
    ensure_resolved: Callable[[Source], None],
    warnings: list[str],
) -> Source | None:
    """A sibling view of ``base`` rooted at a different ``subdir`` of the same repo.

    Reuses the base source's locator (git url+ref, or local path) so the sibling resolves the
    *same* tree, just from another subdirectory. Recorded in the lock only (synthesized), never
    in ``.agentry.yml``. Returned cached if the same sibling was already synthesized this run.
    """
    name = f"{base.name}+{subdir.replace('/', '-')}"
    if name in sources_by_name:
        return sources_by_name[name]
    if base.type is SourceType.GIT:
        sib = Source(name=name, type=SourceType.GIT, url=base.url, ref=base.ref, subdir=subdir)
    else:
        sib = Source(name=name, type=SourceType.LOCAL, path=base.path, subdir=subdir)
    sources_by_name[name] = sib
    try:
        ensure_resolved(sib)
    except ResolveError as exc:
        warnings.append(f"sibling dependency at '{subdir}' of source '{base.name}': {exc}")
        del sources_by_name[name]
        return None
    return sib
