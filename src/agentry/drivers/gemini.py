"""Gemini CLI driver — Google's command-line coding agent.

Project layout under ``.gemini/``; MCP servers and hooks merge into ``.gemini/settings.json``
(JSON, ``mcpServers`` / ``hooks``). Commands are TOML files (``{name}.toml``) — agentry
links the author-supplied file as-is, it does not translate a Markdown command into TOML.

Sources: github.com/google-gemini/gemini-cli docs (subagents, custom-commands,
configuration, skills, hooks).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import MergeDest, TargetSpec
from .base import Driver

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
    ),
)
