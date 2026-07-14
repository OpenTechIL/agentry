"""GitHub Copilot driver — Microsoft's coding agent (VS Code / CLI / cloud).

Skills install to ``.github/skills/{name}/`` (the open Agent Skills standard, shared with
``.claude/skills`` and ``.agents/skills``). Custom agents map to
``.github/agents/{name}.agent.md`` — VS Code detects any ``.md`` under ``.github/agents`` as a
custom agent (the legacy ``.chatmode.md`` was renamed to ``.agent.md``). Reusable prompts, the
slash-command analog, map to ``.github/prompts/{name}.prompt.md``. MCP servers merge into
VS Code's ``.vscode/mcp.json`` under the top-level ``servers`` key (note: **not** ``mcpServers``);
the ``mcpServers`` alias lets a stock ``.mcp.json`` fragment install unchanged.

Copilot has no project-level file convention agentry can target for hooks or tools, so those
types are intentionally omitted (reconcile skips unsupported types). Repo-wide instruction files
(``.github/copilot-instructions.md`` / ``AGENTS.md``) are a single shared document rather than
per-component artifacts — covered by the universal AGENTS.md target, not here.

Sources: code.visualstudio.com/docs (custom-agents, agent-customization/agent-skills, mcp);
docs.github.com/copilot (prompt files, agent skills).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import MergeDest, TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="copilot",
        link={
            _C.SKILL: ".github/skills/{name}",
            _C.AGENT: ".github/agents/{name}.agent.md",
            _C.COMMAND: ".github/prompts/{name}.prompt.md",
        },
        merge={
            _C.MCP: MergeDest(".vscode/mcp.json", "servers", aliases=("mcpServers",)),
        },
        memory_file=".github/copilot-instructions.md",
    ),
)
