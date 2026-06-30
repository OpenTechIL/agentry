"""Kiro driver — AWS's agentic IDE/CLI.

Skills install to ``.kiro/skills/{name}/`` (the open Agent Skills standard — a folder with a
``SKILL.md``; Kiro does **not** recognize a bare ``.md`` placed directly under ``skills/``).
MCP servers merge into the workspace ``.kiro/settings/mcp.json`` under ``mcpServers`` (a
workspace file takes precedence over the global ``~/.kiro/settings/mcp.json``).

Kiro's custom agents live in ``.kiro/agents/`` as **JSON** definitions — a per-tool format
agentry does not translate from a Markdown agent — so ``agent`` is intentionally omitted, as are
``hook`` and ``command`` (no confirmed project-level file convention agentry can target).
Steering documents (``.kiro/steering/*.md``) are repo-wide context, not per-component artifacts.

Sources: kiro.dev/docs (skills, mcp/configuration, cli/custom-agents/configuration-reference).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import MergeDest, TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="kiro",
        link={
            _C.SKILL: ".kiro/skills/{name}",
        },
        merge={
            _C.MCP: MergeDest(".kiro/settings/mcp.json", "mcpServers"),
        },
    ),
)
