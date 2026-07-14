"""Codex CLI driver — OpenAI's command-line coding agent.

Skills install to ``.agents/skills/{name}/`` (Codex reads the shared ``.agents/skills``
convention, *not* ``.codex/skills``). MCP servers merge into ``.codex/config.toml`` under
``[mcp_servers.<id>]`` (snake_case, **not** ``mcpServers``) — the merge installer writes TOML
when the destination ends in ``.toml``, preserving the rest of the user's config. The source
fragment stays JSON; the ``mcpServers`` alias lets a stock ``.mcp.json`` install unchanged.

Codex's agents, custom prompts, and hooks also live in TOML (agents as ``[agents]`` /
per-file tables, hooks as ``[[hooks.Event]]`` array-of-tables). Agent/command definitions
are a per-tool format agentry doesn't translate, and the array-of-tables hook shape doesn't
fit the named-entry merge contract, so both remain unmapped for now.

Sources: developers.openai.com/codex (config-reference, subagents, skills, mcp, hooks).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import MergeDest, TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="codex",
        link={
            _C.SKILL: ".agents/skills/{name}",
        },
        merge={
            _C.MCP: MergeDest(".codex/config.toml", "mcp_servers", aliases=("mcpServers",)),
        },
        memory_file="AGENTS.md",
    ),
)
