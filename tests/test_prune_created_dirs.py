"""#48: removing a link/copy component prunes only the empty folders agentry created.

Folders agentry made for an install are recorded in ``Manifest.created_dirs``; on removal only
those, once completely empty, are removed (deepest first). A folder the user made — even an
empty one — a folder with anything of the user's in it, the project root, ``.agentry/`` and
symlinks are never removed. Old manifests without records leave every folder in place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.manifest import load_manifest, manifest_path
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import sync

_C = ComponentType
_STRATEGIES = ["link", "copy"]


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    skill = src / "skills" / "rev" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: rev\ndescription: d\n---\nbody\n")
    hook = src / "hooks" / "guard.json"
    hook.parent.mkdir(parents=True)
    group = {"hooks": [{"type": "command", "command": "guard"}]}
    hook.write_bytes(json.dumps({"Stop": [group]}).encode("utf-8"))
    return src


def _project(tmp_path: Path, strategy: str = "link", *, hook: bool = False, setup=None) -> Path:
    src = _source(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    if setup:
        setup(proj)  # what the user had before agentry
    store = ConfigStore.create(proj, ["claude"])
    if strategy == "copy":
        store.doc["target_profiles"] = {
            "claude": {"skill": {"strategy": "copy", "dest": ".claude/skills/{name}"}}
        }
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.SKILL, name="rev"))
    if hook:
        store.add_component(Component(source="s", type=_C.HOOK, name="guard"))
    store.save()
    sync(proj)
    assert (proj / ".claude/skills/rev").exists()
    return proj


def _drop(proj: Path, *refs: str) -> None:
    store = ConfigStore.load(proj)
    keep = [
        c for c in store.doc["components"] if f"{c['source']}/{c['type']}/{c['name']}" not in refs
    ]
    store.doc["components"] = keep
    store.save()


def _drop_all(proj: Path) -> None:
    store = ConfigStore.load(proj)
    store.doc["components"] = []
    store.save()


def _tree(proj: Path) -> list[str]:
    """Every path under the project except agentry's own store and config."""
    out = []
    for p in sorted(proj.rglob("*")):
        rel = p.relative_to(proj).as_posix()
        if rel.startswith(".agentry") or rel == ".gitignore":
            continue
        out.append(rel)
    return out


# --- agentry's own folders go -------------------------------------------------------------


@pytest.mark.parametrize("strategy", _STRATEGIES)
def test_agentry_created_parents_removed(tmp_path: Path, strategy: str):
    """Guard (holds today too): folders agentry created for the install are removed."""
    proj = _project(tmp_path, strategy)
    _drop_all(proj)
    sync(proj)
    assert not (proj / ".claude").exists()
    assert proj.is_dir() and (proj / ".agentry").is_dir()  # root and store never pruned


@pytest.mark.parametrize("strategy", _STRATEGIES)
def test_install_records_created_dirs(tmp_path: Path, strategy: str):
    proj = _project(tmp_path, strategy)
    assert {".claude", ".claude/skills"} <= set(load_manifest(proj).created_dirs)
    _drop_all(proj)
    sync(proj)
    assert not {".claude", ".claude/skills"} & set(load_manifest(proj).created_dirs)


# --- the user's folders stay --------------------------------------------------------------


@pytest.mark.parametrize("strategy", _STRATEGIES)
def test_user_created_empty_skills_dir_survives(tmp_path: Path, strategy: str):
    def setup(proj: Path) -> None:
        (proj / ".claude/skills").mkdir(parents=True)  # the user's, empty

    proj = _project(tmp_path, strategy, setup=setup)
    assert not {".claude", ".claude/skills"} & set(load_manifest(proj).created_dirs)
    _drop_all(proj)
    sync(proj)
    assert not (proj / ".claude/skills/rev").exists()  # ours is gone
    assert (proj / ".claude/skills").is_dir()  # the user's empty folder stays
    assert _tree(proj) == [".claude", ".claude/skills"]


@pytest.mark.parametrize("strategy", _STRATEGIES)
def test_user_made_parent_survives_agentry_child_goes(tmp_path: Path, strategy: str):
    def setup(proj: Path) -> None:
        (proj / ".claude").mkdir()  # the user's, empty

    proj = _project(tmp_path, strategy, setup=setup)
    _drop_all(proj)
    sync(proj)
    assert not (proj / ".claude/skills").exists()  # agentry made this one
    assert (proj / ".claude").is_dir()  # the user made this one
    assert _tree(proj) == [".claude"]


@pytest.mark.parametrize("strategy", _STRATEGIES)
def test_parent_with_user_file_inside_survives(tmp_path: Path, strategy: str):
    """Guard (holds today too): a non-empty folder is never removed."""
    proj = _project(tmp_path, strategy)
    (proj / ".claude/notes.md").write_bytes(b"mine\n")
    _drop_all(proj)
    sync(proj)
    assert not (proj / ".claude/skills").exists()
    assert (proj / ".claude/notes.md").read_bytes() == b"mine\n"


def test_symlinked_parent_is_never_removed(tmp_path: Path):
    """A user's `.claude` that is a symlink to a folder elsewhere stays a symlink."""
    real_box: list[Path] = []

    def setup(proj: Path) -> None:
        real = proj / "real-claude"  # inside the project, so installs through it are allowed
        real.mkdir()
        (proj / ".claude").symlink_to(real, target_is_directory=True)
        real_box.append(real)

    proj = _project(tmp_path, setup=setup)
    _drop_all(proj)
    sync(proj)
    link, real = proj / ".claude", real_box[0]
    assert link.is_symlink() and link.resolve() == real.resolve()
    assert real.is_dir()


@pytest.mark.parametrize("strategy", _STRATEGIES)
def test_legacy_manifest_without_records_leaves_dirs(tmp_path: Path, strategy: str):
    """An install from before #48 has no folder records: nothing is inferred, folders stay."""
    proj = _project(tmp_path, strategy)
    raw = json.loads(manifest_path(proj).read_text("utf-8"))
    raw.pop("created_dirs", None)
    raw.pop("created_files", None)
    manifest_path(proj).write_bytes(json.dumps(raw).encode("utf-8"))
    _drop_all(proj)
    sync(proj)
    assert not (proj / ".claude/skills/rev").exists()
    assert (proj / ".claude/skills").is_dir()


# --- shared with a merge-created file (#45) -----------------------------------------------


@pytest.mark.parametrize(
    "first",
    ["skill", "hook"],  # skill first: .claude/ was the link's
)
def test_dir_shared_by_link_and_merge_file_pruned_after_both(tmp_path: Path, first: str):
    """`.claude/` holds agentry's skills/ link and agentry's settings.json: it goes only when
    both are gone, in either order."""
    proj = _project(tmp_path, hook=True)
    assert (proj / ".claude/settings.json").is_file()
    ref = {"skill": "s/skill/rev", "hook": "s/hook/guard"}

    _drop(proj, ref[first])
    sync(proj)
    assert (proj / ".claude").is_dir()  # the other one still lives there
    if first == "skill":
        assert not (proj / ".claude/skills").exists()
        assert (proj / ".claude/settings.json").is_file()
    else:
        assert not (proj / ".claude/settings.json").exists()
        assert (proj / ".claude/skills/rev").exists()

    _drop_all(proj)
    sync(proj)
    assert not (proj / ".claude").exists()


# --- stability ----------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", _STRATEGIES)
@pytest.mark.parametrize("user_dir", [False, True], ids=["agentry_dirs", "user_dir"])
def test_prune_is_idempotent(tmp_path: Path, strategy: str, user_dir: bool):
    setup = (lambda proj: (proj / ".claude/skills").mkdir(parents=True)) if user_dir else None
    proj = _project(tmp_path, strategy, setup=setup)
    _drop_all(proj)
    sync(proj)
    before = _tree(proj)
    again = sync(proj)
    assert again.created == [] and again.updated == [] and again.removed == []
    assert _tree(proj) == before


@pytest.mark.parametrize("strategy", _STRATEGIES)
def test_status_and_doctor_delete_nothing(tmp_path: Path, monkeypatch, strategy: str):
    from typer.testing import CliRunner

    from agentry.cli import app
    from agentry.doctor import run_checks
    from agentry.reconcile import status

    proj = _project(tmp_path, strategy)
    _drop_all(proj)
    before = _tree(proj)
    recorded = load_manifest(proj).created_dirs
    manifest_bytes = manifest_path(proj).read_bytes()
    status(proj)
    run_checks(proj)
    monkeypatch.chdir(proj)
    for cmd in (["status"], ["doctor"]):
        CliRunner().invoke(app, cmd)
    assert _tree(proj) == before
    assert (proj / ".claude/skills/rev").exists()  # only sync removes
    assert load_manifest(proj).created_dirs == recorded  # records untouched too
    assert manifest_path(proj).read_bytes() == manifest_bytes


@pytest.mark.parametrize("strategy", _STRATEGIES)
def test_stale_record_never_claims_a_user_recreated_dir(tmp_path: Path, strategy: str):
    """agentry made `.claude/skills`; the user deletes `.claude`; the next sync drops the
    now-stale records. When the user later recreates an empty `.claude/skills/` by hand, it is
    theirs: installing and removing a skill there leaves it in place."""
    import shutil

    proj = _project(tmp_path, strategy)
    assert ".claude/skills" in load_manifest(proj).created_dirs
    _drop_all(proj)
    shutil.rmtree(proj / ".claude")  # the user removes the whole folder
    sync(proj)
    assert not {".claude", ".claude/skills"} & set(load_manifest(proj).created_dirs)

    (proj / ".claude/skills").mkdir(parents=True)  # recreated by hand, empty
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.SKILL, name="rev"))
    store.save()
    sync(proj)
    assert (proj / ".claude/skills/rev").exists()
    assert not {".claude", ".claude/skills"} & set(load_manifest(proj).created_dirs)

    _drop_all(proj)
    sync(proj)
    assert not (proj / ".claude/skills/rev").exists()
    assert (proj / ".claude/skills").is_dir()  # the user's folder survives
    assert _tree(proj) == [".claude", ".claude/skills"]


def test_stale_created_file_record_dropped(tmp_path: Path):
    """The same for #45's file records: a recorded config file the user deleted is forgotten,
    so a user-made file of that name later is never deleted as agentry's."""
    proj = _project(tmp_path, hook=True)
    assert ".claude/settings.json" in load_manifest(proj).created_files
    _drop_all(proj)
    import shutil

    shutil.rmtree(proj / ".claude")
    sync(proj)
    assert ".claude/settings.json" not in load_manifest(proj).created_files

    (proj / ".claude").mkdir()
    (proj / ".claude/settings.json").write_bytes(b"{}\n")  # the user's, empty skeleton
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.HOOK, name="guard"))
    store.save()
    sync(proj)
    _drop_all(proj)
    sync(proj)
    assert (proj / ".claude/settings.json").is_file()  # theirs: kept though empty
    assert (proj / ".claude").is_dir()


def test_stale_vscode_mcp_json_record_dropped_user_file_survives(tmp_path: Path):
    """agentry creates .vscode/mcp.json; the user deletes it; sync forgets the record. A
    skeleton-identical file the user then makes by hand is theirs and survives removal."""
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "srv.json").write_bytes(json.dumps({"srv": {"command": "x"}}).encode())
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["copilot"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.MCP, name="srv"))
    store.save()
    sync(proj)
    path = proj / ".vscode" / "mcp.json"
    assert ".vscode/mcp.json" in load_manifest(proj).created_files

    path.unlink()  # the user deletes agentry's file
    _drop_all(proj)
    sync(proj)
    assert ".vscode/mcp.json" not in load_manifest(proj).created_files

    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b'{"servers": {}}\n')  # by hand, identical to agentry's skeleton
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.MCP, name="srv"))
    store.save()
    sync(proj)
    assert ".vscode/mcp.json" not in load_manifest(proj).created_files  # pre-existing now
    _drop_all(proj)
    sync(proj)
    assert path.is_file()  # the user's file survives
    assert "srv" not in json.dumps(json.loads(path.read_text("utf-8")))  # only ours removed
