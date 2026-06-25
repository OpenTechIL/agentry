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

import hashlib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import discovery
from .lockfile import upsert_entry
from .models import Component, Config, Dependency, Lock, Source, SourceType
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
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return tail or "dep"


def _synth_name(url: str, taken: set[str]) -> str:
    """A deterministic, collision-free logical name for a transitive git source."""
    base = _repo_basename(url)
    if base not in taken:
        return base
    suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{suffix}"


def resolve_graph(
    root: Path, config: Config, lock: Lock, *, update: bool = False
) -> tuple[DepGraph, Lock]:
    """Resolve every source and the transitive component closure.

    Returns the augmented :class:`DepGraph` and a freshly-built :class:`Lock` (config
    sources plus the synthesized sources actually reached). The caller decides whether to
    persist the lock.
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
        pinned = None if update else (existing.resolved if existing else None)
        entry = resolve(root, src, pinned=pinned)
        entry.synthesized = src.name not in cfg_names
        upsert_entry(new_lock, entry)
        graph.resolved[src.name] = entry.resolved

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
                idx = discovery.index(effective_root(root, dep_src))
                if (dep.type, dep.name) not in idx:
                    graph.warnings.append(
                        f"{comp.ref} requires {dep_ref}, but '{dep.name}' is not provided by "
                        f"source '{dep_src.name}'"
                    )

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
    ensure_resolved,
    warnings: list[str],
) -> Source | None:
    """Map one dependency onto a (possibly synthesized) source, enforcing version policy."""
    if not dep.source and not dep.url:
        return sources_by_name.get(requester_source)  # same source as the requirer

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
        return src

    # url-based — transitive, recorded in the lock only.
    url = dep.url  # validator guarantees url is set when source is not
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
