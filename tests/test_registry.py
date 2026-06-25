from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry import registry as reg
from agentry.cli import app
from agentry.config import ConfigStore
from agentry.models import Config, Registry

runner = CliRunner()


def _skill_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "cool"
    repo.mkdir()
    (repo / "SKILL.md").write_text("# cool\n")
    return repo


def _write_index(tmp_path: Path, skill_repo: Path) -> Path:
    script = (
        "import os;p=os.path.join(os.getcwd(), '.claude/skills/fake');"
        "os.makedirs(p, exist_ok=True);"
        "open(os.path.join(p,'SKILL.md'),'w').write('# fake\\n')"
    )
    index = {
        "version": 1,
        "skills": {
            "cool": {
                "summary": "a cool skill",
                "source": {"type": "local", "path": str(skill_repo)},
                "install": "link",
                "path": ".",
            },
            "fake": {
                "summary": "self-installer",
                "source": {"type": "local", "path": str(skill_repo)},
                "install": "generate",
                "generate": {
                    "command": [sys.executable, "-c", script],
                    "produces": [".claude/skills/fake"],
                },
            },
        },
    }
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index))
    return path


def test_load_index_and_find(tmp_path: Path):
    skill_repo = _skill_repo(tmp_path)
    index_path = _write_index(tmp_path, skill_repo)
    config = Config(registries=[Registry(name="r", location=str(index_path))])

    idx = reg.load_index(tmp_path, config.registries[0])
    assert set(idx.skills) == {"cool", "fake"}

    match = reg.find(tmp_path, config, "cool")
    assert match is not None and match[1].path == "."
    assert reg.find(tmp_path, config, "nope") is None

    listed = {name for _, name, _ in reg.list_skills(tmp_path, config)}
    assert listed == {"cool", "fake"}


def test_invalid_index_errors(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    config = Config(registries=[Registry(name="r", location=str(bad))])
    with pytest.raises(reg.RegistryError, match="invalid index"):
        reg.load_index(tmp_path, config.registries[0])


def test_add_link_skill_from_registry(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    skill_repo = _skill_repo(tmp_path)
    index_path = _write_index(tmp_path, skill_repo)
    monkeypatch.chdir(project)

    assert runner.invoke(app, ["registry", "add", "r", str(index_path)]).exit_code == 0
    result = runner.invoke(app, ["add", "cool"])
    assert result.exit_code == 0, result.output

    link = project / ".claude/skills/cool"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_text() == "# cool\n"
    # Resolved into a real source + component in config.
    cfg = ConfigStore.load(project).parsed()
    assert cfg.source("cool") is not None
    assert cfg.find_component("cool/skill/cool").path == "."


def test_add_generate_skill_gated(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    skill_repo = _skill_repo(tmp_path)
    index_path = _write_index(tmp_path, skill_repo)
    monkeypatch.chdir(project)
    runner.invoke(app, ["registry", "add", "r", str(index_path)])

    # Without --allow-run: added but not executed.
    res = runner.invoke(app, ["add", "fake"])
    assert res.exit_code == 0
    assert not (project / ".claude/skills/fake").exists()
    assert "--allow-run" in res.output

    # With --allow-run on a later sync: executes.
    res2 = runner.invoke(app, ["sync", "--allow-run"])
    assert res2.exit_code == 0, res2.output
    assert (project / ".claude/skills/fake/SKILL.md").read_text() == "# fake\n"


def test_add_unknown_skill_errors(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    index_path = _write_index(tmp_path, _skill_repo(tmp_path))
    monkeypatch.chdir(project)
    runner.invoke(app, ["registry", "add", "r", str(index_path)])
    res = runner.invoke(app, ["add", "ghost"])
    assert res.exit_code == 1
    assert "No skill named 'ghost'" in res.output


def test_add_bare_name_without_registries_errors(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    res = runner.invoke(app, ["add", "graphify"])
    assert res.exit_code == 1
    assert "no registries are configured" in res.output


def test_shipped_curated_index_is_valid():
    # The starter registry committed at registry/skills.json must parse and expose
    # the curated skills with sane install methods.
    from agentry.models import RegistryIndex
    from agentry.models import Strategy

    path = Path(__file__).resolve().parent.parent / "registry" / "skills.json"
    idx = RegistryIndex.model_validate(json.loads(path.read_text()))
    assert "ui-ux-pro-max" in idx.skills
    ui = idx.skills["ui-ux-pro-max"]
    assert ui.install is Strategy.LINK and ui.path == ".claude/skills/ui-ux-pro-max"
    gph = idx.skills["graphify"]
    assert gph.install is Strategy.GENERATE and gph.generate.produces == [".claude/skills/graphify"]


def test_registry_persisted_and_listed(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    index_path = _write_index(tmp_path, _skill_repo(tmp_path))
    monkeypatch.chdir(project)
    runner.invoke(app, ["registry", "add", "r", str(index_path)])

    cfg = ConfigStore.load(project).parsed()
    assert cfg.registries and cfg.registries[0].location == str(index_path)

    out = runner.invoke(app, ["registry", "list"]).output
    assert "cool" in out and "fake" in out
