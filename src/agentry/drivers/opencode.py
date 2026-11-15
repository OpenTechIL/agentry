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
            _C.MCP: MergeDest(
                "opencode.json",
                "mcp",
                aliases=("mcpServers",),
                # OpenCode adds this line itself; it isn't the user's content.
                tool_keys=(("$schema", "https://opencode.ai/config.json"),),
            ),
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
                # Runtime-confirmed (R3, OpenCode 1.19.5): one non-conforming entry makes
                # OpenCode refuse the whole opencode.json, so it is skipped, not installed.
                on_mismatch="skip",
                skip_message=(
                    "{ref}: this MCP server uses Claude's format, which OpenCode rejects; it "
                    "would make OpenCode refuse the whole project config — skipped on opencode. "
                    "Ship {variant_path} (an OpenCode-specific variant of this file), or "
                    "override mcp for opencode under target_profiles."
                ),
                # When no working variant path can be named (an explicit `path:`, a
                # descriptor-placed file), name none rather than one that wouldn't be found.
                skip_message_fallback=(
                    "{ref}: this MCP server uses Claude's format, which OpenCode rejects; it "
                    "would make OpenCode refuse the whole project config — skipped on opencode. "
                    "Override mcp for opencode under target_profiles."
                ),
            ),
        },
    ),
)
