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

import difflib

from .drivers import BUILTIN_DRIVERS
from .drivers.claude import CLAUDE_HOOK_EVENTS
from .models import (
    BUILTIN_TARGET_NAMES,
    ComponentType,
    Config,
    ExpectRule,
    ProfileRule,
    Strategy,
)
from .progname import prog
from .resolver import ResolveError
from .spec import FormatExpectation, LinkMergeDest, MergeDest, TargetSpec

__all__ = [
    "BUILTIN_TARGETS",
    "CLAUDE_HOOK_EVENTS",
    "LinkMergeDest",
    "MergeDest",
    "TargetSpec",
    "filter_claude_hook_events",
    "is_builtin",
    "resolve_targets",
    "UnresolvedTargetError",
    "check_targets",
    "describe_unresolved",
    "suggest_target",
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
    expect = dict(base.expect) if base else {}

    def _clear(ctype: ComponentType, *, keep: dict) -> None:
        for d in (link, copy, merge, link_merge):
            if d is not keep:
                d.pop(ctype, None)

    for ctype, rule in rules.items():
        # Content expectations: omitted ⇒ inherit the built-in; `expect: {}` ⇒ drop it;
        # anything else ⇒ replace it.
        if rule.expect is not None:
            exp = _to_expectation(rule.expect)
            if exp == FormatExpectation():
                expect.pop(ctype, None)
            else:
                expect[ctype] = exp
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
    return TargetSpec(
        name=name,
        link=link,
        copy=copy,
        merge=merge,
        link_merge=link_merge,
        memory_file=base.memory_file if base else None,
        expect=expect,
    )


def _to_expectation(rule: ExpectRule) -> FormatExpectation:
    return FormatExpectation(
        extension=rule.extension,
        events=frozenset(rule.events) if rule.events is not None else None,
        require=tuple(rule.require),
        types=dict(rule.types),
        forbid=tuple(rule.forbid),
        values={k: tuple(v) for k, v in rule.values.items()},
        reject={k: tuple(v) for k, v in rule.reject.items()},
        reject_hint=rule.reject_hint,
        expects=rule.expects,
    )


def resolve_targets(config: Config) -> dict[str, TargetSpec]:
    """Effective target map: built-ins with ``config.target_profiles`` merged over them."""
    specs: dict[str, TargetSpec] = dict(BUILTIN_TARGETS)
    for tname, rules in config.target_profiles.items():
        specs[tname] = _apply_profile(specs.get(tname), tname, rules)
    return specs


def unresolved_targets(config: Config, specs: dict[str, TargetSpec] | None = None) -> list[str]:
    """Active targets that have neither a built-in nor a profile definition."""
    specs = specs if specs is not None else resolve_targets(config)
    return sorted(t for t in config.referenced_targets() if t not in specs)


class UnresolvedTargetError(ResolveError):
    """A target in ``.agentry.yml`` has no built-in, profile, or overlay definition.

    Raised by sync/status *before* anything is resolved or written: syncing with a typo'd
    target (``clade``) would otherwise compute nothing for the real one and uninstall it.
    A :class:`~agentry.resolver.ResolveError`, so the CLI's existing handler exits 1.
    """


def suggest_target(name: str, config: Config) -> str | None:
    """The closest defined target name to ``name`` (built-ins + target_profiles), if any."""
    known = sorted(set(BUILTIN_TARGET_NAMES) | set(config.target_profiles))
    matches = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    return matches[0] if matches else None


def describe_unresolved(name: str, config: Config) -> str:
    """``target 'clade' is not defined — did you mean 'claude'?`` (no clause without a match)."""
    guess = suggest_target(name, config)
    return f"target '{name}' is not defined" + (f" — did you mean '{guess}'?" if guess else "")


def check_targets(config: Config) -> None:
    """Fail fast (before any network call or write) if any active target is undefined.

    Checks the declared config: project ``targets:`` and each declared component's
    ``targets:``. A name is fatal when it is neither built-in nor in ``target_profiles`` —
    a catalog overlay that hasn't been ``target add``-ed yet included (sync can't resolve
    it; the hint points at ``target list`` / ``target add``). No catalog lookup: offline.
    """
    missing = unresolved_targets(config)
    if not missing:
        return
    parts = []
    for t in missing:
        part = describe_unresolved(t, config)
        users = [c.ref for c in config.components if c.targets and t in c.targets]
        if t not in config.targets and users:
            part = f"{', '.join(users)}: {part}"  # point at the component(s) to fix
        parts.append(part if part.endswith("?") else part + ".")
    add = (
        f"`{prog()} target add {missing[0]}`"
        if len(missing) == 1
        else f"`{prog()} target add <name>`"
    )
    raise UnresolvedTargetError(
        f"{' '.join(parts)} Nothing was changed. Fix `targets:` in .agentry.yml, define it "
        f"under target_profiles, or install a driver overlay (`{prog()} target list`, then "
        f"{add})."
    )


def is_builtin(name: str) -> bool:
    return name in BUILTIN_TARGET_NAMES
