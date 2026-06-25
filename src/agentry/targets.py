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
class LinkMergeDest:
    """Composite install: symlink a script dir AND merge its config, rewriting paths.

    ``link_dest`` is a symlink destination template (``{name}`` placeholder) for the
    script directory; ``merge`` says where the config entries go. ``rewrite_from`` /
    ``rewrite_to`` (both optional, ``{name}`` expands in ``rewrite_to``) rewrite a
    command-path prefix in the merged fragment so the symlinked scripts resolve from
    the target tool — e.g. a Claude plugin's ``${CLAUDE_PLUGIN_ROOT}/hooks`` →
    ``${CLAUDE_PROJECT_DIR}/.claude/hooks/{name}``.
    """

    link_dest: str
    merge: MergeDest
    rewrite_from: str = ""
    rewrite_to: str = ""


@dataclass(frozen=True)
class TargetSpec:
    name: str
    #: component type -> symlink destination template (``{name}`` placeholder)
    link: dict[ComponentType, str] = field(default_factory=dict)
    #: component type -> copy destination template (``{name}`` placeholder)
    copy: dict[ComponentType, str] = field(default_factory=dict)
    #: component type -> config-merge destination
    merge: dict[ComponentType, MergeDest] = field(default_factory=dict)
    #: component type -> composite link+merge destination
    link_merge: dict[ComponentType, LinkMergeDest] = field(default_factory=dict)

    def supports(self, ctype: ComponentType) -> bool:
        return (
            ctype in self.link
            or ctype in self.copy
            or ctype in self.merge
            or ctype in self.link_merge
        )

    def strategy(self, ctype: ComponentType) -> Strategy | None:
        if ctype in self.link_merge:
            return Strategy.LINK_MERGE
        if ctype in self.copy:
            return Strategy.COPY
        if ctype in self.link:
            return Strategy.LINK
        if ctype in self.merge:
            return Strategy.MERGE
        return None

    def link_dest(self, ctype: ComponentType, name: str) -> str:
        return self.link[ctype].format(name=name)

    def copy_dest(self, ctype: ComponentType, name: str) -> str:
        return self.copy[ctype].format(name=name)

    def merge_dest(self, ctype: ComponentType) -> MergeDest:
        return self.merge[ctype]

    def link_merge_dest(self, ctype: ComponentType) -> LinkMergeDest:
        return self.link_merge[ctype]


#: Hook events Claude Code recognizes as keys under ``settings.json`` ``hooks``. Kept in
#: one place; may need updating as Claude Code adds events. Used to guard against
#: foreign-harness fragments injecting invalid event keys (e.g. Cursor's ``sessionStart``).
CLAUDE_HOOK_EVENTS: frozenset[str] = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PostToolBatch",
        "Notification",
        "UserPromptSubmit",
        "UserPromptExpansion",
        "SessionStart",
        "SessionEnd",
        "Stop",
        "StopFailure",
        "SubagentStart",
        "SubagentStop",
        "PreCompact",
        "PostCompact",
        "PermissionRequest",
        "PermissionDenied",
        "Setup",
        "TeammateIdle",
        "TaskCreated",
        "TaskCompleted",
        "Elicitation",
        "ElicitationResult",
        "ConfigChange",
        "WorktreeCreate",
        "WorktreeRemove",
        "InstructionsLoaded",
        "CwdChanged",
        "FileChanged",
        "MessageDisplay",
    }
)


def filter_claude_hook_events(entries: dict) -> tuple[dict, list[str]]:
    """Split hook entries into (recognized, dropped-keys) for Claude's settings.json."""
    kept = {k: v for k, v in entries.items() if k in CLAUDE_HOOK_EVENTS}
    dropped = [k for k in entries if k not in CLAUDE_HOOK_EVENTS]
    return kept, dropped


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
