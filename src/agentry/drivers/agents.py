"""Universal Agent-Skills target — the tool-neutral ``.agents/`` layout.

Many agents read the shared Agent Skills convention: a skill is a ``{name}/SKILL.md`` folder
under ``.agents/skills/`` (the same standard behind ``.claude/skills`` and ``.github/skills``).
Installing to this single ``agents`` target makes a skill portable to *any* harness that
honors the convention — Codex and Kimi already read ``.agents/skills`` — without committing to
one specific tool. This is agentry's universality lever: one target, many consumers.

Only ``skill`` is mapped (``.agents/skills/{name}``). There is no tool-neutral directory
standard for agents, commands, hooks, or MCP, and ``AGENTS.md`` itself is a single repo-root
instruction *document* rather than a set of per-component artifacts — composing components into
one ``AGENTS.md`` is a content transformation reserved for the ``transform`` seam (see
:mod:`agentry.drivers.base`), not file placement, so it is intentionally out of scope here.

Sources: agentskills.io; the shared AGENTS.md convention (agents.md).
"""

from __future__ import annotations

from ..models import ComponentType as _C
from ..spec import TargetSpec
from .base import Driver

DRIVER = Driver(
    spec=TargetSpec(
        name="agents",
        link={
            _C.SKILL: ".agents/skills/{name}",
        },
        memory_file="AGENTS.md",
    ),
)
