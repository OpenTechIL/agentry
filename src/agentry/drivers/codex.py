"""Codex CLI driver — OpenAI's command-line coding agent.

Skills install to ``.agents/skills/{name}/`` (Codex reads the shared ``.agents/skills``
convention, *not* ``.codex/skills``). Codex's agents, custom prompts, MCP servers, and hooks
all live in **TOML** (``.codex/config.toml`` — MCP under ``[mcp_servers.<id>]``, snake_case,
not ``mcpServers``). agentry's merge installer is JSON-only, so those are deferred until a
TOML-merge strategy exists; only the (portable) skill type is mapped today.

Sources: developers.openai.com/codex (config-reference, subagents, skills, mcp, hooks).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="codex",
        link={
            _C.SKILL: ".agents/skills/{name}",
        },
    ),
)
