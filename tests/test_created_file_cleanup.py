"""#45: a config file agentry created is deleted once its last agentry entry is removed.

Only when the manifest records that agentry created it (``created_files``), it parses, and it
is semantically agentry's empty skeleton. Anything else — a user key, a comment, a parse
failure, a file that existed before, an old manifest without the record — keeps the file.
Folders agentry created for it go too (deepest first), never one that pre-existed or still
holds anything. JSON (`.mcp.json`, `.vscode/mcp.json`) and TOML (`.codex/config.toml`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.manifest import load_manifest, manifest_path
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import sync

# target -> (config file, folders agentry creates for it, deepest first)
_CASES = {
    "claude": (".mcp.json", []),
    "copilot": (".vscode/mcp.json", [".vscode"]),
    "codex": (".codex/config.toml", [".codex"]),
}
_DEEP = "deep/a/b/mcp.json"
_DEEP_PROFILE = {"deeptool": {"mcp": {"strategy": "merge", "file": _DEEP, "pointer": "mcpServers"}}}


def _project(tmp_path: Path, target: str, profiles: dict | None = None) -> Path:
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "srv.json").write_text(json.dumps({"srv": {"command": "x"}}), "utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, [target])
    if profiles:
        store.doc["target_profiles"] = profiles
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.MCP, name="srv"))
    store.save()
    return proj


def _drop_components(proj: Path) -> None:
    store = ConfigStore.load(proj)
    store.doc["components"] = []
    store.save()


def _readd(proj: Path) -> None:
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=ComponentType.MCP, name="srv"))
    store.save()


def _line(file: str) -> str:
    return f"remove {file} (created by agentry, now empty)"


def _edit(path: Path, *, toml: str, json_fn) -> None:
    if path.suffix == ".toml":
        path.write_bytes((path.read_text("utf-8") + toml).encode("utf-8"))
    else:
        doc = json.loads(path.read_text("utf-8"))
        json_fn(doc)
        path.write_bytes(json.dumps(doc).encode("utf-8"))


# --- the happy path ------------------------------------------------------------------------


@pytest.mark.parametrize("target", list(_CASES))
def test_created_file_and_dirs_deleted_when_emptied(tmp_path: Path, target: str):
    file, dirs = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    assert (proj / file).is_file()
    m = load_manifest(proj)
    assert file in m.created_files and set(dirs) <= set(m.created_dirs)

    _drop_components(proj)
    res = sync(proj)
    assert not (proj / file).exists()
    for d in dirs:
        assert not (proj / d).exists()
    assert res.removed.count(_line(file)) == 1
    m = load_manifest(proj)
    assert file not in m.created_files and not set(dirs) & set(m.created_dirs)


@pytest.mark.parametrize("target", list(_CASES))
def test_cleanup_is_idempotent(tmp_path: Path, target: str):
    file, _ = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    _drop_components(proj)
    sync(proj)
    again = sync(proj)
    assert again.created == [] and again.updated == [] and again.removed == []
    assert not (proj / file).exists()


@pytest.mark.parametrize("target", list(_CASES))
def test_readd_after_deletion_recreates_and_records(tmp_path: Path, target: str):
    file, dirs = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    _drop_components(proj)
    sync(proj)
    _readd(proj)
    sync(proj)
    assert (proj / file).is_file()
    m = load_manifest(proj)
    assert file in m.created_files and set(dirs) <= set(m.created_dirs)
    _drop_components(proj)  # and it cleans up again
    sync(proj)
    assert not (proj / file).exists()


# --- the file stays ------------------------------------------------------------------------


@pytest.mark.parametrize("target", list(_CASES))
def test_user_key_added_keeps_file(tmp_path: Path, target: str):
    file, dirs = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    _edit(proj / file, toml='\n[user]\nmine = "yes"\n', json_fn=lambda d: d.update(user=1))

    _drop_components(proj)
    res = sync(proj)
    assert (proj / file).is_file()
    for d in dirs:
        assert (proj / d).is_dir()
    text = (proj / file).read_text("utf-8")
    assert "mine" in text if file.endswith(".toml") else json.loads(text)["user"] == 1
    assert "srv" not in text  # agentry's own entry is still removed
    assert _line(file) not in res.removed


@pytest.mark.parametrize("target", list(_CASES))
@pytest.mark.parametrize(
    ("toml", "json_key"),
    [("\n[other]\n", {"other": {}}), ("\nenabled = false\n", {"enabled": False})],
    ids=["empty-table", "falsy-value"],
)
def test_extra_key_with_empty_or_falsy_value_keeps_file(tmp_path, target, toml, json_key):
    """Semantic equality with the skeleton, not "all values empty": an extra key the user
    added stays theirs even when its value is `{}` or `false`."""
    file, _ = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    _edit(proj / file, toml=toml, json_fn=lambda d: d.update(json_key))
    _drop_components(proj)
    res = sync(proj)
    assert (proj / file).is_file()
    assert _line(file) not in res.removed


@pytest.mark.parametrize("target", ["codex", "copilot"])
def test_comment_added_keeps_file(tmp_path: Path, target: str):
    """A comment is user content. (In .vscode/mcp.json a `//` comment makes the file
    unparseable for agentry today, which keeps it too.)"""
    file, _ = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    path = proj / file
    comment = "# mine\n" if file.endswith(".toml") else "// mine\n"
    path.write_bytes((comment + path.read_text("utf-8")).encode("utf-8"))

    _drop_components(proj)
    res = sync(proj)
    assert path.is_file() and "mine" in path.read_text("utf-8")
    assert _line(file) not in res.removed


@pytest.mark.parametrize("target", list(_CASES))
def test_unparseable_file_kept(tmp_path: Path, target: str):
    file, _ = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    garbage = b"{{{ not json or toml ===\n"
    (proj / file).write_bytes(garbage)

    _drop_components(proj)
    res = sync(proj)  # warns about the unreadable file (T10), never deletes it
    assert (proj / file).read_bytes() == garbage
    assert _line(file) not in res.removed


@pytest.mark.parametrize("target", list(_CASES))
def test_preexisting_file_kept_when_emptied(tmp_path: Path, target: str):
    file, dirs = _CASES[target]
    proj = _project(tmp_path, target)
    path = proj / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"" if file.endswith(".toml") else b"{}\n")  # the user's, empty

    sync(proj)
    m = load_manifest(proj)
    assert file not in m.created_files and not set(dirs) & set(m.created_dirs)
    _drop_components(proj)
    res = sync(proj)
    assert path.is_file()
    for d in dirs:
        assert (proj / d).is_dir()  # pre-existing folders are never removed
    assert _line(file) not in res.removed


@pytest.mark.parametrize("target", list(_CASES))
def test_old_manifest_without_record_keeps_file(tmp_path: Path, target: str):
    """An install from before #45 has no record: nothing is inferred, the file stays."""
    file, dirs = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    raw = json.loads(manifest_path(proj).read_text("utf-8"))
    raw.pop("created_files", None)
    raw.pop("created_dirs", None)
    manifest_path(proj).write_text(json.dumps(raw), "utf-8")

    _drop_components(proj)
    res = sync(proj)
    assert (proj / file).is_file()
    for d in dirs:
        assert (proj / d).is_dir()
    assert _line(file) not in res.removed


# --- nested folders ------------------------------------------------------------------------


def test_nested_created_dirs_removed_deepest_first(tmp_path: Path):
    proj = _project(tmp_path, "deeptool", _DEEP_PROFILE)
    sync(proj)
    assert set(load_manifest(proj).created_dirs) >= {"deep", "deep/a", "deep/a/b"}
    _drop_components(proj)
    res = sync(proj)
    assert not (proj / "deep").exists()
    assert res.removed.count(_line(_DEEP)) == 1
    assert load_manifest(proj).created_dirs == []


def test_preexisting_parent_dir_never_removed(tmp_path: Path):
    proj = _project(tmp_path, "deeptool", _DEEP_PROFILE)
    (proj / "deep").mkdir()  # the user's folder, empty
    sync(proj)
    assert "deep" not in load_manifest(proj).created_dirs
    _drop_components(proj)
    sync(proj)
    assert not (proj / "deep/a").exists()  # agentry's own folders go
    assert (proj / "deep").is_dir()  # the user's stays, though empty


def test_created_dir_holding_another_file_stays(tmp_path: Path):
    """Stop at the first non-empty folder (hidden files count), keeping its parents too."""
    proj = _project(tmp_path, "deeptool", _DEEP_PROFILE)
    sync(proj)
    (proj / "deep/a/.keep").write_bytes(b"")  # a hidden file the user dropped in
    _drop_components(proj)
    sync(proj)
    assert not (proj / _DEEP).exists()
    assert not (proj / "deep/a/b").exists()  # emptied: removed
    assert (proj / "deep/a/.keep").is_file()  # non-empty: kept, with its parent
    assert (proj / "deep").is_dir()
    assert sync(proj).removed == []  # and stable


def test_file_shared_by_two_components_waits_for_the_last(tmp_path: Path):
    """Removing one of two entries leaves the file; removing the last deletes it."""
    proj = _project(tmp_path, "claude")
    src = tmp_path / "src"
    (src / "mcp" / "two.json").write_text(json.dumps({"two": {"command": "y"}}), "utf-8")
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=ComponentType.MCP, name="two"))
    store.save()
    sync(proj)

    store = ConfigStore.load(proj)
    store.doc["components"] = [c for c in store.doc["components"] if c["name"] == "two"]
    store.save()
    res = sync(proj)
    assert json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"].keys() == {"two"}
    assert _line(".mcp.json") not in res.removed

    _drop_components(proj)
    assert sync(proj).removed.count(_line(".mcp.json")) == 1
    assert not (proj / ".mcp.json").exists()


def test_cli_prints_the_removal_line(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from agentry.cli import app

    proj = _project(tmp_path, "copilot")
    sync(proj)
    _drop_components(proj)
    monkeypatch.chdir(proj)
    out = CliRunner(env={"COLUMNS": "200"}).invoke(app, ["sync"]).output
    assert "  - remove .vscode/mcp.json (created by agentry, now empty)\n" in out


def test_preexisting_empty_vscode_dir_kept(tmp_path: Path):
    """The user's empty `.vscode/` (made before agentry created .vscode/mcp.json) is never
    removed, even though agentry's file inside it is."""
    proj = _project(tmp_path, "copilot")
    (proj / ".vscode").mkdir()
    sync(proj)
    m = load_manifest(proj)
    assert ".vscode/mcp.json" in m.created_files and ".vscode" not in m.created_dirs
    _drop_components(proj)
    res = sync(proj)
    assert res.removed.count(_line(".vscode/mcp.json")) == 1
    assert (proj / ".vscode").is_dir() and not any((proj / ".vscode").iterdir())


def test_toml_left_with_only_a_user_comment_is_kept(tmp_path: Path):
    proj = _project(tmp_path, "codex")
    sync(proj)
    path = proj / ".codex/config.toml"
    path.write_bytes(("# my note\n" + path.read_text("utf-8")).encode("utf-8"))
    _drop_components(proj)
    res = sync(proj)
    text = path.read_text("utf-8")
    assert "# my note" in text and "srv" not in text  # ours gone, only the comment's left
    assert _line(".codex/config.toml") not in res.removed
    assert (proj / ".codex").is_dir()


def test_user_key_added_then_removed_deletes(tmp_path: Path):
    """PM: once the user's key is gone again, nothing of theirs is left, so the file is
    agentry's skeleton and goes."""
    proj = _project(tmp_path, "claude")
    sync(proj)
    path = proj / ".mcp.json"
    _edit(path, toml="", json_fn=lambda d: d.update(user=1))
    sync(proj)
    _edit(path, toml="", json_fn=lambda d: d.pop("user"))
    _drop_components(proj)
    assert sync(proj).removed.count(_line(".mcp.json")) == 1
    assert not path.exists()


@pytest.mark.parametrize("target", list(_CASES))
def test_status_and_doctor_never_delete(tmp_path: Path, monkeypatch, target: str):
    """Only sync deletes: after the components go, status and doctor leave the emptied file
    (and its folders) alone; the next sync removes them."""
    from typer.testing import CliRunner

    from agentry.cli import app
    from agentry.doctor import run_checks
    from agentry.reconcile import status

    file, dirs = _CASES[target]
    proj = _project(tmp_path, target)
    sync(proj)
    _drop_components(proj)
    before = (proj / file).read_bytes()
    status(proj)
    run_checks(proj)
    monkeypatch.chdir(proj)
    for cmd in (["status"], ["doctor"]):
        CliRunner().invoke(app, cmd)
    assert (proj / file).read_bytes() == before
    for d in dirs:
        assert (proj / d).is_dir()
    assert file in load_manifest(proj).created_files
    sync(proj)
    assert not (proj / file).exists()
