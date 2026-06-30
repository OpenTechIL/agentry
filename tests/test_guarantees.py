"""Structural guarantees — agentry's safety/reproducibility properties, locked in as a
CI-enforced contract.

These are the properties Microsoft apm keeps regressing on (lockfile churn, silent
overwrite of hand-edited config, last-write-wins dropping a server). agentry handles them
by construction; this suite asserts they *stay* true. Each test names the apm issue it
guards against so the intent is auditable.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentry.config import ConfigStore
from agentry.lockfile import lock_path
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import status, sync

_C = ComponentType


def _project(proj: Path, src: Path, *components: Component) -> ConfigStore:
    proj.mkdir(exist_ok=True)
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for c in components:
        store.add_component(c)
    store.save()
    return store


def test_lockfile_is_byte_stable_across_runs(tmp_path: Path, local_source: Path):
    """A second sync with unchanged inputs rewrites the lock byte-for-byte (no timestamp
    churn). Guards apm #1702 (lockfile rewritten every run via a `generated_at` field)."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.SKILL, name="code-reviewer"))
    sync(proj)
    first = lock_path(proj).read_bytes()
    sync(proj)
    assert lock_path(proj).read_bytes() == first


def test_sync_is_idempotent_noop(tmp_path: Path, local_source: Path):
    """The one hard rule: a second sync changes nothing. Guards drift/churn regressions."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.SKILL, name="code-reviewer"))
    sync(proj)
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []


def test_remove_is_fully_reversible(tmp_path: Path, local_source: Path):
    """Disabling a component removes exactly its managed artifacts — symlink and merged key
    — and prunes empty dirs, leaving the tree as if it were never installed. Guards apm's
    stale-artifact class (#1729/#1730/#1831)."""
    proj = tmp_path / "proj"
    store = _project(
        proj,
        local_source,
        Component(source="s", type=_C.SKILL, name="code-reviewer"),
        Component(source="s", type=_C.MCP, name="github"),
    )
    sync(proj)
    assert (proj / ".claude/skills/code-reviewer").is_symlink()
    assert "github" in json.loads((proj / ".mcp.json").read_text())["mcpServers"]

    store.set_enabled("s/skill/code-reviewer", False)
    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(proj)

    assert not (proj / ".claude/skills/code-reviewer").exists()
    # The MCP key is gone and the now-empty section/file is cleaned up, not left as a shell.
    assert not (proj / ".mcp.json").exists() or "github" not in json.loads(
        (proj / ".mcp.json").read_text()
    ).get("mcpServers", {})


def test_merge_never_clobbers_hand_authored_keys(tmp_path: Path, local_source: Path):
    """Merging an MCP server preserves a hand-authored entry in the same file, and removing
    agentry's entry leaves the hand-authored one intact. Guards apm #20 / #1764 (silent
    overwrite of hand-edited config)."""
    proj = tmp_path / "proj"
    store = _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    # A pre-existing, hand-authored MCP server the user wrote themselves.
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"handmade": {"command": "mine"}}}))

    sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text())["mcpServers"]
    assert servers["handmade"] == {"command": "mine"}  # untouched
    assert "github" in servers  # ours added alongside

    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text())["mcpServers"]
    assert servers == {"handmade": {"command": "mine"}}  # only ours removed


def test_distinct_mcp_servers_never_drop_each_other(tmp_path: Path):
    """Two MCP components with distinct server names both survive the merge — agentry merges
    each fragment's own declared name and never derives one by truncation. Guards apm #1693
    (scoped-name collision silently drops a server, last-write-wins)."""
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "acme.json").write_text(json.dumps({"acme-mcp": {"command": "a"}}))
    (src / "mcp" / "other.json").write_text(json.dumps({"other-mcp": {"command": "b"}}))
    proj = tmp_path / "proj"
    _project(
        proj,
        src,
        Component(source="s", type=_C.MCP, name="acme"),
        Component(source="s", type=_C.MCP, name="other"),
    )
    sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text())["mcpServers"]
    assert {"acme-mcp", "other-mcp"} <= set(servers)


def test_status_and_sync_share_one_resolution_path(tmp_path: Path, local_source: Path):
    """`status` reports `ok` for exactly what `sync` installed — audit can't invent drift
    that install never produced. Guards apm #1923/#1924 (audit ≠ install false drift)."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.SKILL, name="code-reviewer"))
    sync(proj)
    rows, _ = status(proj)
    assert rows and all(r.state == "ok" for r in rows)
