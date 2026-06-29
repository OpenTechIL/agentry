"""Cursor driver — rules-only for file components.

Markdown agents/commands map to ``.cursor/rules/{name}.mdc``; skills/tools (directories)
and hooks are unsupported (a type absent from the map is skipped by reconcile). MCP is
merged into ``.cursor/mcp.json``.
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import MergeDest, TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="cursor",
        link={
            _C.AGENT: ".cursor/rules/{name}.mdc",
            _C.COMMAND: ".cursor/rules/{name}.mdc",
        },
        merge={
            _C.MCP: MergeDest(".cursor/mcp.json", "mcpServers"),
        },
    ),
)
