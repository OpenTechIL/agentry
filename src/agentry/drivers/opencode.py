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
        },
        # OpenCode loads only *.ts / *.js from .opencode/tools/, and a symlinked tool that
        # imports @opencode-ai/plugin crashes every opencode command (R3, 1.19.5): tools keep
        # their extension and install as copies (sync refreshes them).
        copy={
            _C.TOOL: ".opencode/tools/{name}{ext}",
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
            # OpenCode loads only top-level *.ts / *.js in .opencode/tools/ (R3, 1.19.5): a
            # directory tool (dirtool/index.ts) or a single file of another kind (.py, no
            # extension) would be silently ignored, so it's skipped here.
            _C.TOOL: FormatExpectation(
                file_only=True,
                extension=(".ts", ".js"),
                expects="a single .ts or .js file",
                on_mismatch="skip",
                # `--path` resolves under the source's effective root (its --subdir
                # included); `{src_dir}` is the folder the tool came from (`tools/`, or the
                # folder of its explicit `path:`), so the suggested path is right as printed.
                skip_message=(
                    "{ref}: OpenCode only loads single-file tools (.ts/.js directly in "
                    ".opencode/tools/); this tool isn't one, so it was skipped on "
                    "opencode. Ship it as one file, {src_dir}{name}.ts or .js, and add it "
                    "with `agentry add {source}/tool/{name} --path {src_dir}{name}.ts`, or "
                    "list it in the source's agentry.yaml."
                ),
            ),
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
