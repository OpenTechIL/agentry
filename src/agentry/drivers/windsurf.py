"""Windsurf driver — Cascade's project layout under ``.windsurf/``.

Skills install to ``.windsurf/skills/{name}/``; *workflows* are Windsurf's slash commands,
so the ``command`` type maps to ``.windsurf/workflows/{name}.md``. Hooks merge into
``.windsurf/hooks.json``. Windsurf has **no** custom agent-definition format (subagents are
runtime-only) and project-level MCP is undocumented (user-level ``mcp_config.json`` only),
so both ``agent`` and ``mcp`` are intentionally omitted — reconcile skips unsupported types.

Sources: docs.windsurf.com/windsurf/cascade (workflows, skills, hooks, mcp, agents-md).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import MergeDest, TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="windsurf",
        link={
            _C.SKILL: ".windsurf/skills/{name}",
            _C.COMMAND: ".windsurf/workflows/{name}.md",
        },
        merge={
            _C.HOOK: MergeDest(".windsurf/hooks.json", "hooks"),
        },
    ),
)
