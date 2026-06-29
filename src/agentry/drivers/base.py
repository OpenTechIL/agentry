"""The :class:`Driver` abstraction — one *kind of AI agent* (Claude Code, Gemini CLI, …).

A driver is the **target side** of agentry's two-sided model: given the canonical
components a source provides, it says *how* and *where* they install into one tool. It
**composes** a :class:`~agentry.spec.TargetSpec` (the pure capability map) with optional
per-agent *policies* for the handful of behaviors that are not just path placement:

* :class:`HookEventPolicy` — validate hook-event keys before they reach a tool's config
  (Claude Code rejects a settings file containing unknown event keys).
* :class:`NamespacePolicy` — which component types nest under a ``<repo>/`` subfolder
  (Claude turns ``.claude/commands/<repo>/adr.md`` into the ``/<repo>:adr`` slash command).
* ``transform`` — a reserved seam for *semantic translation* (e.g. rewriting a fragment
  between JSON/TOML/YAML or between tool formats). Default ``None`` means placement-only;
  a future driver can set it without any engine rearchitecture.

Targets are an **open set**: any string is a valid tool defined purely in
``.agentry.yml`` via ``target_profiles``. Such a tool gets a default ``Driver`` (just its
merged spec, no policies), so the data-driven escape hatch keeps working unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..models import ComponentType
from ..spec import TargetSpec


@dataclass(frozen=True)
class NamespacePolicy:
    """Component types whose install dir the agent namespaces by ``<repo>/`` subfolder."""

    types: frozenset[ComponentType] = frozenset()

    def applies(self, ctype: ComponentType) -> bool:
        return ctype in self.types


@dataclass(frozen=True)
class HookEventPolicy:
    """Validate hook-event keys against the events an agent recognizes.

    ``enabled=False`` (the default) accepts every key untouched — the behavior for agents
    that don't need guarding. When enabled, unknown keys are split out so the caller can
    warn and skip them rather than write a config the tool would reject wholesale.
    """

    known_events: frozenset[str] = frozenset()
    enabled: bool = False

    def filter(self, entries: dict) -> tuple[dict, list[str]]:
        if not self.enabled:
            return entries, []
        kept = {k: v for k, v in entries.items() if k in self.known_events}
        dropped = [k for k in entries if k not in self.known_events]
        return kept, dropped


#: Reserved seam for future semantic translation of a component fragment for one agent.
#: Takes ``(component_type, fragment)`` and returns the fragment to install. ``None`` (the
#: default everywhere today) means placement-mapping only — no transformation.
TransformFn = Callable[[ComponentType, dict], dict]


@dataclass(frozen=True)
class Driver:
    """One AI agent's install behavior: a capability map plus optional policies."""

    spec: TargetSpec
    namespacing: NamespacePolicy = field(default_factory=NamespacePolicy)
    hook_events: HookEventPolicy = field(default_factory=HookEventPolicy)
    #: Future semantic-translation hook; ``None`` = placement-mapping only (see TransformFn).
    transform: TransformFn | None = None

    @property
    def name(self) -> str:
        return self.spec.name

    # -- capability-map pass-throughs (delegate to the wrapped TargetSpec) --

    def supports(self, ctype: ComponentType) -> bool:
        return self.spec.supports(ctype)

    def strategy(self, ctype: ComponentType):
        return self.spec.strategy(ctype)

    def link_dest(self, ctype: ComponentType, name: str) -> str:
        return self.spec.link_dest(ctype, name)

    def copy_dest(self, ctype: ComponentType, name: str) -> str:
        return self.spec.copy_dest(ctype, name)

    def merge_dest(self, ctype: ComponentType):
        return self.spec.merge_dest(ctype)

    def link_merge_dest(self, ctype: ComponentType):
        return self.spec.link_merge_dest(ctype)

    # -- policy pass-throughs ----------------------------------------------

    def filter_hook_events(self, entries: dict) -> tuple[dict, list[str]]:
        """Split hook entries into ``(recognized, dropped-keys)`` for this agent."""
        return self.hook_events.filter(entries)

    def namespaces(self, ctype: ComponentType) -> bool:
        """Whether this agent nests ``ctype`` installs under a ``<repo>/`` subfolder."""
        return self.namespacing.applies(ctype)
