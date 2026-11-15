"""Q1 (PM ruling on R3): OpenCode's own `$schema` line doesn't keep an emptied opencode.json.

OpenCode writes `"$schema": "https://opencode.ai/config.json"` into the project's opencode.json
on startup. The opencode target declares that exact key/value as *tool-written* (data on the
driver), so the #45 skeleton check ignores it: an agentry-created file left with only that line
is deleted. Value-matched: any other `$schema` value, or any user key, keeps the file. Other
targets declare nothing, so a `$schema` there always keeps the file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.manifest import manifest_path
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync

_SCHEMA = "https://opencode.ai/config.json"
_OC = {"fs": {"type": "local", "command": ["npx", "-y", "fs"]}}
_CL = {"fs": {"command": "npx", "args": ["-y", "fs"]}}
_FILES = {"opencode": "opencode.json", "claude": ".mcp.json", "copilot": ".vscode/mcp.json"}


def _project(tmp_path: Path, target: str) -> Path:
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    body = _OC if target == "opencode" else _CL
    (src / "mcp" / "fs.json").write_bytes(json.dumps(body).encode("utf-8"))
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, [target])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.MCP, name="fs"))
    store.save()
    sync(proj)
    assert (proj / _FILES[target]).is_file()
    return proj


def _tool_writes(proj: Path, file: str, **keys) -> None:
    """What the tool (or the user) does to the file after agentry created it."""
    path = proj / file
    doc = json.loads(path.read_text("utf-8"))
    doc = {**keys, **doc}  # OpenCode puts `$schema` first
    path.write_bytes(json.dumps(doc, indent=2).encode("utf-8"))


def _drop(proj: Path) -> None:
    store = ConfigStore.load(proj)
    store.doc["components"] = []
    store.save()


def _line(file: str) -> str:
    return f"remove {file} (created by agentry, now empty)"


# --- deleted ----------------------------------------------------------------------------


def test_created_opencode_json_with_only_opencode_schema_is_deleted(tmp_path: Path):
    proj = _project(tmp_path, "opencode")
    _tool_writes(proj, "opencode.json", **{"$schema": _SCHEMA})
    _drop(proj)
    res = sync(proj)
    assert not (proj / "opencode.json").exists()
    assert res.removed.count(_line("opencode.json")) == 1


def test_deletion_with_schema_is_idempotent(tmp_path: Path):
    proj = _project(tmp_path, "opencode")
    _tool_writes(proj, "opencode.json", **{"$schema": _SCHEMA})
    _drop(proj)
    sync(proj)
    again = sync(proj)
    assert again.created == [] and again.updated == [] and again.removed == []
    assert not (proj / "opencode.json").exists()


# --- kept ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["https://opencode.ai/config.json#mine", "https://example.invalid/schema.json", ""],
    ids=["suffixed", "other-url", "empty"],
)
def test_different_schema_value_keeps_the_file(tmp_path: Path, value: str):
    """Value-matched: only OpenCode's exact URL counts as tool-written."""
    proj = _project(tmp_path, "opencode")
    _tool_writes(proj, "opencode.json", **{"$schema": value})
    _drop(proj)
    res = sync(proj)
    doc = json.loads((proj / "opencode.json").read_text("utf-8"))
    assert doc["$schema"] == value
    assert "fs" not in doc.get("mcp", {})  # agentry's own entry is still removed
    assert _line("opencode.json") not in res.removed


def test_schema_plus_user_key_keeps_the_file(tmp_path: Path):
    proj = _project(tmp_path, "opencode")
    _tool_writes(proj, "opencode.json", **{"$schema": _SCHEMA, "theme": "mine"})
    _drop(proj)
    res = sync(proj)
    doc = json.loads((proj / "opencode.json").read_text("utf-8"))
    assert doc["$schema"] == _SCHEMA and doc["theme"] == "mine"
    assert _line("opencode.json") not in res.removed


@pytest.mark.parametrize("target", ["claude", "copilot"])
def test_other_targets_have_no_schema_allowance(tmp_path: Path, target: str):
    """The allowance is the opencode target's data: elsewhere, even OpenCode's exact `$schema`
    line is user content and keeps the file."""
    file = _FILES[target]
    proj = _project(tmp_path, target)
    _tool_writes(proj, file, **{"$schema": _SCHEMA})
    _drop(proj)
    res = sync(proj)
    assert json.loads((proj / file).read_text("utf-8"))["$schema"] == _SCHEMA
    assert _line(file) not in res.removed


def test_preexisting_opencode_json_with_only_schema_is_kept(tmp_path: Path):
    """#45's first condition still holds: a file agentry didn't create is never deleted."""
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "fs.json").write_bytes(json.dumps(_OC).encode("utf-8"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "opencode.json").write_bytes(json.dumps({"$schema": _SCHEMA}).encode("utf-8"))
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.MCP, name="fs"))
    store.save()
    sync(proj)
    _drop(proj)
    sync(proj)
    assert json.loads((proj / "opencode.json").read_text("utf-8")) == {"$schema": _SCHEMA}


# --- read-only commands -------------------------------------------------------------------


def test_status_and_doctor_are_read_only(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, "opencode")
    _tool_writes(proj, "opencode.json", **{"$schema": _SCHEMA})
    _drop(proj)
    snap = ((proj / "opencode.json").read_bytes(), manifest_path(proj).read_bytes())
    status(proj)
    run_checks(proj)
    monkeypatch.chdir(proj)
    for cmd in (["status"], ["doctor"]):
        CliRunner().invoke(app, cmd)
    assert ((proj / "opencode.json").read_bytes(), manifest_path(proj).read_bytes()) == snap


def test_profile_tool_keys_empty_clears_the_allowance(tmp_path: Path):
    """A profile `tool_keys: {}` removes the built-in allowance: `$schema` then keeps the file."""
    proj = _project(tmp_path, "opencode")
    store = ConfigStore.load(proj)
    store.doc["target_profiles"] = {
        "opencode": {
            "mcp": {"strategy": "merge", "file": "opencode.json", "pointer": "mcp", "tool_keys": {}}
        }
    }
    store.save()
    _tool_writes(proj, "opencode.json", **{"$schema": _SCHEMA})
    _drop(proj)
    res = sync(proj)
    assert json.loads((proj / "opencode.json").read_text("utf-8")) == {"$schema": _SCHEMA}
    assert _line("opencode.json") not in res.removed


def test_pointer_only_override_inherits_the_schema_tool_key(tmp_path: Path):
    """A target_profiles override that only restates placement (strategy/file/pointer,
    no `tool_keys`) inherits the built-in `$schema` allowance for the same file."""
    proj = _project(tmp_path, "opencode")
    store = ConfigStore.load(proj)
    store.doc["target_profiles"] = {
        "opencode": {"mcp": {"strategy": "merge", "file": "opencode.json", "pointer": "mcp"}}
    }
    store.save()
    _tool_writes(proj, "opencode.json", **{"$schema": _SCHEMA})
    _drop(proj)
    assert sync(proj).removed.count(_line("opencode.json")) == 1
    assert not (proj / "opencode.json").exists()


def test_nested_schema_key_is_not_a_tool_key(tmp_path: Path):
    """Top-level only: a `$schema` inside agentry's pointer section is content, not ignored."""
    proj = _project(tmp_path, "opencode")
    path = proj / "opencode.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["mcp"]["$schema"] = _SCHEMA
    path.write_bytes(json.dumps(doc).encode("utf-8"))
    _drop(proj)
    res = sync(proj)
    assert path.is_file()
    assert _line("opencode.json") not in res.removed
