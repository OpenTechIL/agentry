"""Claude Code driver — Anthropic's CLI/IDE coding agent.

Project layout: ``.claude/`` for skills/agents/commands/tools, hooks merged into
``.claude/settings.json``, MCP servers merged into ``.mcp.json``. Carries the two
Claude-specific policies: hook-event validation (Claude rejects a settings file with
unknown event keys) and command/agent namespacing (``.claude/commands/<repo>/adr.md``
→ ``/<repo>:adr``).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import MergeDest, TargetSpec
from .base import Driver, HookEventPolicy, NamespacePolicy

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

DRIVER = Driver(
    spec=TargetSpec(
        name="claude",
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
        memory_file=".claude/CLAUDE.md",
    ),
    # Commands at .claude/commands/<repo>/adr.md are invoked as /<repo>:adr; agents
    # discover recursively so a subfolder just tidies them. Skills/tools stay flat.
    namespacing=NamespacePolicy(frozenset({_C.COMMAND, _C.AGENT})),
    hook_events=HookEventPolicy(CLAUDE_HOOK_EVENTS, enabled=True),
)
