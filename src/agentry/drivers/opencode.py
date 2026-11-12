"""OpenCode driver — the ``.opencode/`` layout; MCP merged into ``opencode.json``.

The ``mcp`` config accepts the Claude-style ``mcpServers`` wrapper as an alias so a
plugin's stock ``.mcp.json`` fragment installs without reshaping. No hook or namespacing
policy — plain placement.
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import FormatExpectation, MergeDest, TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="opencode",
        link={
            _C.SKILL: ".opencode/skills/{name}",
            _C.AGENT: ".opencode/agents/{name}.md",
            _C.COMMAND: ".opencode/commands/{name}.md",
            _C.TOOL: ".opencode/tools/{name}",
        },
        merge={
            _C.MCP: MergeDest("opencode.json", "mcp", aliases=("mcpServers",)),
        },
        memory_file="AGENTS.md",
        # opencode.ai/config.json: local/remote MCP entries need `type`, take `command` as one
        # array and `environment` (not Claude's `args` / `env`), and reject unknown fields.
        expect={
            _C.MCP: FormatExpectation(
                require=("type",),
                types={"command": "array"},
                forbid=("args", "env"),
                values={"type": ("local", "remote")},
                expects='`type: "local"` with `command` as one array and `environment`',
            ),
        },
    ),
)
