"""Built-in drivers and driver resolution.

A *driver* is one kind of AI agent (see :mod:`agentry.drivers.base`). Each built-in agent
ships as a module exposing a ``DRIVER``; :data:`BUILTIN_DRIVERS` registers them by name.

:func:`resolve_drivers` is the bridge between the data-driven *target* layer and the
driver layer. It reuses :func:`agentry.targets.resolve_targets` — the single place that
deep-merges the project's ``target_profiles`` over the built-in capability maps — then
re-attaches each built-in agent's policies to its (possibly overridden) spec. A tool that
exists *only* in ``target_profiles`` gets a default no-policy :class:`Driver`, so the
YAML-only escape hatch keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import replace

from ..models import Config
from . import agents, claude, codex, copilot, cursor, gemini, kimi, kiro, opencode, windsurf
from .base import Driver, HookEventPolicy, NamespacePolicy, TransformFn

#: Built-in agent drivers, by target name. Override or extend per project via
#: ``.agentry.yml`` -> ``target_profiles``.
BUILTIN_DRIVERS: dict[str, Driver] = {
    claude.DRIVER.name: claude.DRIVER,
    opencode.DRIVER.name: opencode.DRIVER,
    cursor.DRIVER.name: cursor.DRIVER,
    codex.DRIVER.name: codex.DRIVER,
    gemini.DRIVER.name: gemini.DRIVER,
    windsurf.DRIVER.name: windsurf.DRIVER,
    kimi.DRIVER.name: kimi.DRIVER,
    copilot.DRIVER.name: copilot.DRIVER,
    kiro.DRIVER.name: kiro.DRIVER,
    agents.DRIVER.name: agents.DRIVER,
}


def resolve_drivers(config: Config) -> dict[str, Driver]:
    """Effective driver map: built-in policies attached to ``target_profiles``-merged specs.

    Imported lazily to avoid an import cycle (``targets`` re-exports the capability-map
    dataclasses *and* derives its ``BUILTIN_TARGETS`` from this package).
    """
    from ..targets import resolve_targets

    out: dict[str, Driver] = {}
    for name, spec in resolve_targets(config).items():
        base = BUILTIN_DRIVERS.get(name)
        out[name] = replace(base, spec=spec) if base is not None else Driver(spec=spec)
    return out


__all__ = [
    "BUILTIN_DRIVERS",
    "Driver",
    "HookEventPolicy",
    "NamespacePolicy",
    "TransformFn",
    "resolve_drivers",
]
