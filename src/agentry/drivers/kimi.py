"""Kimi (Kimi Code CLI) driver — Moonshot AI's coding agent.

Skills install to ``.kimi-code/skills/{name}/`` (Kimi also reads the shared ``.agents/skills``
convention). MCP servers merge into the dedicated ``.kimi-code/mcp.json`` (``mcpServers``).
Kimi has no user-defined slash commands and no auto-discovery directory for agents (they are
referenced by path), so ``command`` and ``agent`` are intentionally omitted. Hooks live in
``config.toml`` (TOML ``[[hooks]]``) — deferred until agentry has a TOML-merge strategy.

Sources: moonshotai.github.io/kimi-cli and /kimi-code docs (skills, mcp, agents, slash-commands).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import MergeDest, TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="kimi",
        link={
            _C.SKILL: ".kimi-code/skills/{name}",
        },
        merge={
            _C.MCP: MergeDest(".kimi-code/mcp.json", "mcpServers"),
        },
        memory_file="AGENTS.md",
    ),
)
