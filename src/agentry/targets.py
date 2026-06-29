"""Target capability map — built-in defaults, overridable/extendable via config.

Each target declares, per :class:`ComponentType`, how a component installs: a **link**
destination (path template, ``{name}`` substituted) or a **merge** destination (a JSON
config file + the top-level key entries live under). The capability-map dataclasses
(:class:`TargetSpec`, :class:`MergeDest`, :class:`LinkMergeDest`) live in
:mod:`agentry.spec` and are re-exported here for backward compatibility.

The built-in maps are owned by the per-agent driver modules in :mod:`agentry.drivers`;
``BUILTIN_TARGETS`` is derived from them so there is a single source of truth.

``resolve_targets(config)`` returns the *effective* map: the built-ins with the project's
``target_profiles`` deep-merged over them (overriding existing keys and adding new tools).
A component type absent from a target's map is *unsupported* — reconcile skips it.
"""

from __future__ import annotations

from .drivers import BUILTIN_DRIVERS
from .drivers.claude import CLAUDE_HOOK_EVENTS
from .models import (
    BUILTIN_TARGET_NAMES,
    ComponentType,
    Config,
    ProfileRule,
    Strategy,
)
from .spec import LinkMergeDest, MergeDest, TargetSpec

__all__ = [
    "BUILTIN_TARGETS",
    "CLAUDE_HOOK_EVENTS",
    "LinkMergeDest",
    "MergeDest",
    "TargetSpec",
    "filter_claude_hook_events",
    "is_builtin",
    "resolve_targets",
    "unresolved_targets",
]


def filter_claude_hook_events(entries: dict) -> tuple[dict, list[str]]:
    """Split hook entries into (recognized, dropped-keys) for Claude's settings.json.

    Backward-compatible shim: the canonical logic now lives on the claude driver's
    :class:`~agentry.drivers.base.HookEventPolicy`.
    """
    return BUILTIN_DRIVERS["claude"].filter_hook_events(entries)


#: Built-in defaults, derived from the per-agent drivers. Override or extend via
#: `.agentry.yml` -> target_profiles.
BUILTIN_TARGETS: dict[str, TargetSpec] = {name: d.spec for name, d in BUILTIN_DRIVERS.items()}


def _apply_profile(
    base: TargetSpec | None, name: str, rules: dict[ComponentType, ProfileRule]
) -> TargetSpec:
    link = dict(base.link) if base else {}
    copy = dict(base.copy) if base else {}
    merge = dict(base.merge) if base else {}
    link_merge = dict(base.link_merge) if base else {}

    def _clear(ctype: ComponentType, *, keep: dict) -> None:
        for d in (link, copy, merge, link_merge):
            if d is not keep:
                d.pop(ctype, None)

    for ctype, rule in rules.items():
        if rule.strategy is Strategy.LINK:
            link[ctype] = rule.dest  # type: ignore[assignment]
            _clear(ctype, keep=link)
        elif rule.strategy is Strategy.COPY:
            copy[ctype] = rule.dest  # type: ignore[assignment]
            _clear(ctype, keep=copy)
        elif rule.strategy is Strategy.LINK_MERGE:
            link_merge[ctype] = LinkMergeDest(
                rule.dest,  # type: ignore[arg-type]
                MergeDest(rule.file, rule.pointer),  # type: ignore[arg-type]
                rule.rewrite_from or "",
                rule.rewrite_to or "",
            )
            _clear(ctype, keep=link_merge)
        else:
            merge[ctype] = MergeDest(rule.file, rule.pointer)  # type: ignore[arg-type]
            _clear(ctype, keep=merge)
    return TargetSpec(name=name, link=link, copy=copy, merge=merge, link_merge=link_merge)


def resolve_targets(config: Config) -> dict[str, TargetSpec]:
    """Effective target map: built-ins with ``config.target_profiles`` merged over them."""
    specs: dict[str, TargetSpec] = dict(BUILTIN_TARGETS)
    for tname, rules in config.target_profiles.items():
        specs[tname] = _apply_profile(specs.get(tname), tname, rules)
    return specs


def unresolved_targets(config: Config, specs: dict[str, TargetSpec] | None = None) -> list[str]:
    """Active targets that have neither a built-in nor a profile definition."""
    specs = specs if specs is not None else resolve_targets(config)
    return sorted(t for t in config.active_targets() if t not in specs)


def is_builtin(name: str) -> bool:
    return name in BUILTIN_TARGET_NAMES
