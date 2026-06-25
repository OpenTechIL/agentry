from __future__ import annotations

from pathlib import Path

from agentry import discovery
from agentry.models import ComponentType


def test_discover_all_types(local_source: Path):
    found = {(d.type, d.name) for d in discovery.discover(local_source)}
    assert (ComponentType.SKILL, "code-reviewer") in found
    assert (ComponentType.AGENT, "planner") in found
    assert (ComponentType.COMMAND, "deploy") in found
    assert (ComponentType.TOOL, "fmt") in found
    assert (ComponentType.HOOK, "pre-commit-fmt") in found
    assert (ComponentType.MCP, "github") in found
    assert len(found) == 6


def test_artifact_path(local_source: Path):
    p = discovery.artifact_path(local_source, ComponentType.SKILL, "code-reviewer")
    assert p.is_dir()
    p = discovery.artifact_path(local_source, ComponentType.AGENT, "planner")
    assert p.is_file() and p.suffix == ".md"
