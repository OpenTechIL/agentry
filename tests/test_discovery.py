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


def test_discovers_root_mcp_json(tmp_path: Path):
    """A plugin-style root `.mcp.json` is surfaced as a single `mcp` component."""
    root = tmp_path / "plugin"
    root.mkdir()
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"weather": {"type": "http", "url": "https://example.com/mcp"}}}'
    )
    found = {(d.type, d.name): d.path for d in discovery.discover(root)}
    assert (ComponentType.MCP, "mcp") in found
    assert found[(ComponentType.MCP, "mcp")] == root / ".mcp.json"


def test_mcp_json_unprefixed_also_found(tmp_path: Path):
    """`mcp.json` (no leading dot) at the root works too."""
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "mcp.json").write_text('{"mcpServers": {"x": {"type": "http", "url": "u"}}}')
    found = {(d.type, d.name) for d in discovery.discover(root)}
    assert (ComponentType.MCP, "mcp") in found


def test_root_mcp_does_not_duplicate_mcp_dir(tmp_path: Path):
    """An `mcp/mcp.json` already claiming the `mcp` name wins; the root file is skipped."""
    root = tmp_path / "plugin"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "mcp.json").write_text('{"a": {"command": "x"}}')
    (root / ".mcp.json").write_text('{"mcpServers": {"b": {"type": "http", "url": "u"}}}')
    mcp = [d for d in discovery.discover(root) if d.type is ComponentType.MCP and d.name == "mcp"]
    assert len(mcp) == 1
    assert mcp[0].path == root / "mcp" / "mcp.json"
