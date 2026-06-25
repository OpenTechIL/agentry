"""Target capability map — built-in defaults, overridable/extendable via config.

Each target declares, per :class:`ComponentType`, how a component installs: a **link**
destination (path template, ``{name}`` substituted) or a **merge** destination (a JSON
config file + the top-level key entries live under).

``resolve_targets(config)`` returns the *effective* map: the built-ins with the project's
``target_profiles`` deep-merged over them (overriding existing keys and adding new tools).
A component type absent from a target's map is *unsupported* — reconcile skips it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    BUILTIN_TARGET_NAMES,
    ComponentType,
    Config,
    ProfileRule,
    Strategy,
    Target,
)


@dataclass(frozen=True)
class MergeDest:
    file: str  # config file path relative to project root
    pointer: str  # top-level JSON key entries are merged under
    #: Extra top-level keys (besides ``pointer``) a *source* fragment may wrap its
    #: named entries under, to be unwrapped on merge. Plugin/MCP files in the wild
    #: often ship entries wrapped under the section name they target — usually the
    #: same as ``pointer`` (handled implicitly), but e.g. an OpenCode ``mcp`` config
    #: may carry the Claude-style ``mcpServers`` wrapper.
    aliases: tuple[str, ...] = ()

    @property
    def wrapper_keys(self) -> tuple[str, ...]:
        """Top-level keys that, in a source fragment, wrap the real named entries."""
        return (self.pointer, *self.aliases)


@dataclass(frozen=True)
class TargetSpec:
    name: str
    #: component type -> symlink destination template (``{name}`` placeholder)
    link: dict[ComponentType, str] = field(default_factory=dict)
    #: component type -> config-merge destination
    merge: dict[ComponentType, MergeDest] = field(default_factory=dict)

    def supports(self, ctype: ComponentType) -> bool:
        return ctype in self.link or ctype in self.merge

    def strategy(self, ctype: ComponentType) -> Strategy | None:
        if ctype in self.link:
            return Strategy.LINK
        if ctype in self.merge:
            return Strategy.MERGE
        return None

    def link_dest(self, ctype: ComponentType, name: str) -> str:
        return self.link[ctype].format(name=name)

    def merge_dest(self, ctype: ComponentType) -> MergeDest:
        return self.merge[ctype]


_C = ComponentType

#: Built-in defaults. Override or extend via `.agentry.yml` -> target_profiles.
BUILTIN_TARGETS: dict[str, TargetSpec] = {
    Target.CLAUDE: TargetSpec(
        name=Target.CLAUDE,
        link={
            _C.SKILL: ".claude/skills/{name}",
            _C.AGENT: ".claude/agents/{name}.md",
            _C.COMMAND: ".claude/commands/{name}.md",
            _C.TOOL: ".claude/tools/{name}",
        },
        merge={
            _C.HOOK: MergeDest(".claude/settings.json", "hooks"),
            _C.MCP: MergeDest(".mcp.json", "mcpServers"),
        },
    ),
    Target.OPENCODE: TargetSpec(
        name=Target.OPENCODE,
        link={
            _C.SKILL: ".opencode/skills/{name}",
            _C.AGENT: ".opencode/agents/{name}.md",
            _C.COMMAND: ".opencode/commands/{name}.md",
            _C.TOOL: ".opencode/tools/{name}",
        },
        merge={
            _C.MCP: MergeDest("opencode.json", "mcp", aliases=("mcpServers",)),
        },
    ),
    Target.CURSOR: TargetSpec(
        name=Target.CURSOR,
        # Cursor is rules-only for file components: markdown agents/commands map to
        # .cursor/rules/*.mdc. Skills/tools (directories) and hooks are unsupported.
        link={
            _C.AGENT: ".cursor/rules/{name}.mdc",
            _C.COMMAND: ".cursor/rules/{name}.mdc",
        },
        merge={
            _C.MCP: MergeDest(".cursor/mcp.json", "mcpServers"),
        },
    ),
}


def _apply_profile(base: TargetSpec | None, name: str, rules: dict[ComponentType, ProfileRule]) -> TargetSpec:
    link = dict(base.link) if base else {}
    merge = dict(base.merge) if base else {}
    for ctype, rule in rules.items():
        if rule.strategy is Strategy.LINK:
            link[ctype] = rule.dest  # type: ignore[assignment]
            merge.pop(ctype, None)
        else:
            merge[ctype] = MergeDest(rule.file, rule.pointer)  # type: ignore[arg-type]
            link.pop(ctype, None)
    return TargetSpec(name=name, link=link, merge=merge)


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
