"""#100: removing agentry's last entry deletes the empty pointer key only if agentry created it.

A user's own `{"servers": {}}` (or `[mcp_servers]` in Codex TOML) is theirs: after agentry
adds and removes an entry under it, it is still there. A pointer key agentry itself added to
a user's file goes once empty. An older install without the record keeps the key (nothing
is inferred). A file agentry created is still deleted whole (#45).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomlkit

from agentry.config import ConfigStore
from agentry.manifest import manifest_path
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import sync

# target -> (config file, pointer key)
_JSON = {"claude": (".mcp.json", "mcpServers"), "copilot": (".vscode/mcp.json", "servers")}
_TOML = ("codex", ".codex/config.toml", "mcp_servers")


def _project(tmp_path: Path, target: str, *, before: bytes | None = None) -> Path:
    """`before`: the user's own config file content, written before agentry runs."""
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "srv.json").write_bytes(json.dumps({"srv": {"command": "x"}}).encode())
    proj = tmp_path / "proj"
    proj.mkdir()
    if before is not None:
        file = _JSON[target][0] if target in _JSON else _TOML[1]
        (proj / file).parent.mkdir(parents=True, exist_ok=True)
        (proj / file).write_bytes(before)
    store = ConfigStore.create(proj, [target])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.MCP, name="srv"))
    store.save()
    sync(proj)
    return proj


def _remove(proj: Path) -> None:
    store = ConfigStore.load(proj)
    store.doc["components"] = []
    store.save()
    sync(proj)


def _json(proj: Path, file: str) -> dict:
    return json.loads((proj / file).read_text("utf-8"))


# --- a user-created pointer survives ------------------------------------------------------


@pytest.mark.parametrize("target", list(_JSON))
def test_user_created_json_pointer_survives(tmp_path: Path, target: str):
    file, ptr = _JSON[target]
    proj = _project(tmp_path, target, before=json.dumps({ptr: {}}).encode())
    assert "srv" in _json(proj, file)[ptr]
    _remove(proj)
    assert _json(proj, file) == {ptr: {}}


def test_user_created_toml_pointer_survives(tmp_path: Path):
    target, file, ptr = _TOML
    proj = _project(tmp_path, target, before=b'model = "mine"\n\n[mcp_servers]\n')
    assert "srv" in tomlkit.parse((proj / file).read_text("utf-8"))[ptr]
    _remove(proj)
    doc = tomlkit.parse((proj / file).read_text("utf-8"))
    assert doc["model"] == "mine"
    assert ptr in doc and dict(doc[ptr]) == {}


@pytest.mark.parametrize("target", list(_JSON))
def test_user_pointer_survives_two_components(tmp_path: Path, target: str):
    """Ownership of the key doesn't pass to agentry because two of its entries lived there."""
    file, ptr = _JSON[target]
    proj = _project(tmp_path, target, before=json.dumps({ptr: {}}).encode())
    (tmp_path / "src/mcp/two.json").write_bytes(json.dumps({"two": {"command": "y"}}).encode())
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=ComponentType.MCP, name="two"))
    store.save()
    sync(proj)
    assert set(_json(proj, file)[ptr]) == {"srv", "two"}
    _remove(proj)
    assert _json(proj, file) == {ptr: {}}


# --- an agentry-created pointer goes ------------------------------------------------------


@pytest.mark.parametrize("target", list(_JSON))
def test_agentry_created_json_pointer_removed(tmp_path: Path, target: str):
    """Guard (holds today): the user's file had no pointer key; agentry's goes once empty."""
    file, ptr = _JSON[target]
    proj = _project(tmp_path, target, before=b'{"other": 1}\n')
    assert "srv" in _json(proj, file)[ptr]
    _remove(proj)
    assert _json(proj, file) == {"other": 1}


def test_agentry_created_toml_pointer_removed(tmp_path: Path):
    target, file, ptr = _TOML
    proj = _project(tmp_path, target, before=b'model = "mine"\n')
    _remove(proj)
    doc = tomlkit.parse((proj / file).read_text("utf-8"))
    assert doc["model"] == "mine" and ptr not in doc


# --- an older install keeps the key ------------------------------------------------------

# What a pre-#100 manifest can contain. Anything else is #100's new record and is stripped,
# whatever backend calls it, to simulate an install made before the change.
_OLD_TOP = {"version", "links", "copies", "merges", "generated", "link_merges", "transforms"}
_OLD_TOP |= {"created_files", "created_dirs"}
_OLD_MERGE = {"component", "target", "file", "pointer", "keys", "entries"}
_OLD_ENTRY = {"mode", "fingerprint", "preexisting", "kept", "items", "user_items", "created_key"}


def _as_old_manifest(proj: Path) -> None:
    raw = json.loads(manifest_path(proj).read_text("utf-8"))
    raw = {k: v for k, v in raw.items() if k in _OLD_TOP}
    merges = []
    for rec in raw.get("merges", []):
        rec = {k: v for k, v in rec.items() if k in _OLD_MERGE}
        rec["entries"] = {
            key: {k: v for k, v in entry.items() if k in _OLD_ENTRY}
            for key, entry in rec.get("entries", {}).items()
        }
        merges.append(rec)
    raw["merges"] = merges
    manifest_path(proj).write_bytes(json.dumps(raw).encode("utf-8"))


@pytest.mark.parametrize("target", list(_JSON))
def test_older_install_without_record_keeps_pointer(tmp_path: Path, target: str):
    """agentry did add the key here, but an old manifest can't prove it: keep it."""
    file, ptr = _JSON[target]
    proj = _project(tmp_path, target, before=b'{"other": 1}\n')
    _as_old_manifest(proj)
    _remove(proj)
    assert _json(proj, file) == {"other": 1, ptr: {}}


# --- #45 still applies -------------------------------------------------------------------


@pytest.mark.parametrize("target", [*_JSON, "codex"])
def test_agentry_created_file_still_deleted(tmp_path: Path, target: str):
    file = _JSON[target][0] if target in _JSON else _TOML[1]
    proj = _project(tmp_path, target)
    assert (proj / file).is_file()
    _remove(proj)
    assert not (proj / file).exists()


# --- stability ---------------------------------------------------------------------------


@pytest.mark.parametrize("user_pointer", [True, False], ids=["user_pointer", "agentry_pointer"])
@pytest.mark.parametrize("target", list(_JSON))
def test_pointer_cleanup_idempotent(tmp_path: Path, target: str, user_pointer: bool):
    file, ptr = _JSON[target]
    before = {ptr: {}} if user_pointer else {"other": 1}
    proj = _project(tmp_path, target, before=json.dumps(before).encode())
    _remove(proj)
    after = (proj / file).read_bytes()
    again = sync(proj)
    assert again.created == [] and again.updated == [] and again.removed == []
    assert (proj / file).read_bytes() == after


@pytest.mark.parametrize("target", list(_JSON))
def test_status_and_doctor_modify_nothing(tmp_path: Path, monkeypatch, target: str):
    from typer.testing import CliRunner

    from agentry.cli import app
    from agentry.doctor import run_checks
    from agentry.reconcile import status

    file, ptr = _JSON[target]
    proj = _project(tmp_path, target, before=json.dumps({ptr: {}}).encode())
    store = ConfigStore.load(proj)
    store.doc["components"] = []
    store.save()  # removed from config, not yet synced
    snap = ((proj / file).read_bytes(), manifest_path(proj).read_bytes())
    status(proj)
    run_checks(proj)
    monkeypatch.chdir(proj)
    for cmd in (["status"], ["doctor"]):
        CliRunner().invoke(app, cmd)
    assert ((proj / file).read_bytes(), manifest_path(proj).read_bytes()) == snap


def _set_components(proj: Path, *names: str) -> None:
    store = ConfigStore.load(proj)
    store.doc["components"] = []
    for n in names:
        store.add_component(Component(source="s", type=ComponentType.MCP, name=n))
    store.save()


@pytest.mark.parametrize("target", list(_JSON))
def test_pointer_recreated_by_hand_is_the_users(tmp_path: Path, target: str):
    """agentry created the pointer; the user deleted it and a sync ran (forgetting agentry's
    claim); the user then re-added an empty pointer by hand — that one is theirs."""
    file, ptr = _JSON[target]
    proj = _project(tmp_path, target, before=b'{"other": 1}\n')
    doc = _json(proj, file)
    del doc[ptr]  # the user deletes the pointer key (and our entry with it)
    (proj / file).write_bytes(json.dumps(doc).encode("utf-8"))
    _set_components(proj)
    sync(proj)
    assert _json(proj, file) == {"other": 1}

    (proj / file).write_bytes(json.dumps({"other": 1, ptr: {}}).encode("utf-8"))  # by hand
    _set_components(proj, "srv")
    sync(proj)
    assert "srv" in _json(proj, file)[ptr]
    _remove(proj)
    assert _json(proj, file) == {"other": 1, ptr: {}}


@pytest.mark.parametrize("target", list(_JSON))
def test_shared_agentry_created_pointer_pops_after_last(tmp_path: Path, target: str):
    """Guard: two components under a pointer agentry created — removing the first keeps the
    pointer with the second's entry; removing the second pops it."""
    file, ptr = _JSON[target]
    proj = _project(tmp_path, target, before=b'{"other": 1}\n')
    (tmp_path / "src/mcp/two.json").write_bytes(json.dumps({"two": {"command": "y"}}).encode())
    _set_components(proj, "srv", "two")
    sync(proj)
    assert set(_json(proj, file)[ptr]) == {"srv", "two"}

    _set_components(proj, "two")
    sync(proj)
    assert _json(proj, file) == {"other": 1, ptr: {"two": {"command": "y"}}}

    _remove(proj)
    assert _json(proj, file) == {"other": 1}
