"""Tests for the TOML destination of the merge installer (Phase B — Codex config.toml)."""

from __future__ import annotations

import json
from pathlib import Path

import tomlkit

from agentry.config import ConfigStore
from agentry.installers import merge as m
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync
from agentry.spec import MergeDest

_C = ComponentType
DEST = MergeDest(".codex/config.toml", "mcp_servers", aliases=("mcpServers",))


def test_install_merge_writes_toml_section(tmp_path: Path):
    keys = m.install_merge(tmp_path, DEST, {"github": {"command": "npx", "args": ["-y", "srv"]}})
    assert keys == ["github"]
    doc = tomlkit.parse((tmp_path / ".codex/config.toml").read_text())
    assert doc["mcp_servers"]["github"]["command"] == "npx"
    assert doc["mcp_servers"]["github"]["args"] == ["-y", "srv"]


def test_merge_preserves_user_config_and_other_servers(tmp_path: Path):
    cfg = tmp_path / ".codex/config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '# my codex config\nmodel = "gpt-5"\n\n[mcp_servers.handwritten]\ncommand = "keep-me"\n'
    )
    m.install_merge(tmp_path, DEST, {"github": {"command": "npx"}})

    text = cfg.read_text()
    assert "# my codex config" in text  # comment preserved
    assert 'model = "gpt-5"' in text  # unrelated setting preserved
    doc = tomlkit.parse(text)
    assert doc["mcp_servers"]["handwritten"]["command"] == "keep-me"  # hand-added survives
    assert doc["mcp_servers"]["github"]["command"] == "npx"  # agentry entry added


def test_remove_merge_strips_only_owned_keys(tmp_path: Path):
    cfg = tmp_path / ".codex/config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.handwritten]\ncommand = "keep-me"\n')
    m.install_merge(tmp_path, DEST, {"github": {"command": "npx"}})

    assert m.remove_merge(tmp_path, DEST, ["github"]) is True
    doc = tomlkit.parse(cfg.read_text())
    assert "github" not in doc["mcp_servers"]
    assert doc["mcp_servers"]["handwritten"]["command"] == "keep-me"  # untouched


def test_merge_state_toml(tmp_path: Path):
    assert m.merge_state(tmp_path, DEST, ["github"]) == "missing"
    m.install_merge(tmp_path, DEST, {"github": {"command": "npx"}})
    assert m.merge_state(tmp_path, DEST, ["github"]) == "ok"
    assert m.merge_state(tmp_path, DEST, ["github", "absent"]) == "missing"


def test_section_dropped_when_emptied(tmp_path: Path):
    m.install_merge(tmp_path, DEST, {"github": {"command": "npx"}})
    m.remove_merge(tmp_path, DEST, ["github"])
    doc = tomlkit.parse((tmp_path / ".codex/config.toml").read_text())
    assert "mcp_servers" not in doc


# -- end-to-end through the codex driver ---------------------------------


def test_sync_codex_mcp_into_config_toml(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, ["codex"]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=_C.SKILL, name="code-reviewer"))
    store.add_component(Component(source="s", type=_C.MCP, name="github"))
    store.save()

    sync(proj)
    assert (proj / ".agents/skills/code-reviewer").is_symlink()
    doc = tomlkit.parse((proj / ".codex/config.toml").read_text())
    assert "github" in doc["mcp_servers"]

    # Idempotent: a second sync is a no-op and status reports ok.
    res2 = sync(proj)
    assert res2.created == [] and res2.updated == [] and res2.removed == []
    rows, _ = status(proj)
    assert all(r.state == "ok" for r in rows)


def test_sync_codex_mcp_removed_on_disable(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, ["codex"]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=_C.MCP, name="github"))
    store.save()
    sync(proj)
    assert "github" in tomlkit.parse((proj / ".codex/config.toml").read_text())["mcp_servers"]

    # Disable and re-sync: agentry's entry is stripped, file remains valid TOML.
    store2 = ConfigStore.load(proj)
    store2.set_enabled("s/mcp/github", False)
    store2.save()
    sync(proj)
    doc = tomlkit.parse((proj / ".codex/config.toml").read_text())
    assert "mcp_servers" not in doc or "github" not in doc.get("mcp_servers", {})


def test_json_merge_unaffected_by_toml_path(tmp_path: Path):
    # Regression guard: JSON destinations still round-trip as JSON.
    jdest = MergeDest(".mcp.json", "mcpServers")
    m.install_merge(tmp_path, jdest, {"github": {"command": "npx"}})
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["github"]["command"] == "npx"
