"""Gemini CLI driver — Google's command-line coding agent.

Project layout under ``.gemini/``; MCP servers and hooks merge into ``.gemini/settings.json``
(JSON, ``mcpServers`` / ``hooks``). Commands are TOML files (``{name}.toml``) — agentry
links the author-supplied file as-is, it does not translate a Markdown command into TOML.

Sources: github.com/google-gemini/gemini-cli docs (subagents, custom-commands,
configuration, skills, hooks).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import FormatExpectation, MergeDest, TargetSpec
from .base import Driver, HookEventPolicy

#: Hook events Gemini CLI recognizes under ``settings.json`` ``hooks`` (docs/hooks/reference.md).
#: Claude's names (``PreToolUse``, ``Stop``, …) are not among them, so a Claude-authored hook
#: merged here never fires. Phase A only warns; flipping the policy below to ``enabled=True``
#: would drop unknown events instead (phase B).
GEMINI_HOOK_EVENTS: frozenset[str] = frozenset(
    {
        "BeforeTool",
        "AfterTool",
        "BeforeAgent",
        "AfterAgent",
        "BeforeModel",
        "BeforeToolSelection",
        "AfterModel",
        "SessionStart",
        "SessionEnd",
        "Notification",
        "PreCompress",
    }
)

DRIVER = Driver(
    spec=TargetSpec(
        name="gemini",
        link={
            _C.SKILL: ".gemini/skills/{name}",
            _C.AGENT: ".gemini/agents/{name}.md",
            _C.COMMAND: ".gemini/commands/{name}.toml",
        },
        merge={
            _C.MCP: MergeDest(".gemini/settings.json", "mcpServers"),
            _C.HOOK: MergeDest(".gemini/settings.json", "hooks"),
        },
        memory_file="GEMINI.md",
        expect={
            _C.COMMAND: FormatExpectation(
                extension=".toml", expects="a TOML command file with a `prompt` field"
            ),
            # G3: Gemini has no `type`; an entry with `url` is SSE and streamable HTTP uses
            # `httpUrl`, so a Claude-style {type: "http", url} silently connects over SSE.
            _C.MCP: FormatExpectation(
                reject={"type": ("http", "streamable-http")},
                reject_hint="Gemini reads `url` as SSE; use `httpUrl` for streamable HTTP",
                expects="`httpUrl` for HTTP servers (`url` means SSE)",
            ),
            _C.HOOK: FormatExpectation(
                events=GEMINI_HOOK_EVENTS,
                expects="Gemini hook event names (BeforeTool, AfterTool, SessionStart, …)",
            ),
        },
    ),
    hook_events=HookEventPolicy(GEMINI_HOOK_EVENTS, enabled=False),
)
