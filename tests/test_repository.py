from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry import registry as reg
from agentry.cli import app
from agentry.config import ConfigStore
from agentry.models import Config, Registry

runner = CliRunner()


def _repo_with_skill_and_mcp(tmp_path: Path) -> Path:
    """A source repo holding a skill AND a (root) MCP server — both via discovery."""
    repo = tmp_path / "toolkit"
    (repo / "skills" / "reviewer").mkdir(parents=True)
    (repo / "skills" / "reviewer" / "SKILL.md").write_text("# reviewer\n")
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"type": "http", "url": "https://example.com/mcp"}}})
    )
    return repo


def _catalog(tmp_path: Path, repo: Path, *, expose=None) -> Path:
    entry = {"summary": "a toolkit", "source": {"type": "local", "path": str(repo)}}
    if expose is not None:
        entry["expose"] = expose
    path = tmp_path / "repositories.json"
    path.write_text(json.dumps({"version": 1, "repositories": {"toolkit": entry}}))
    return path


def test_load_catalog_and_find_repo(tmp_path: Path):
    repo = _repo_with_skill_and_mcp(tmp_path)
    cat = _catalog(tmp_path, repo)
    config = Config(repositories=[Registry(name="c", location=str(cat))])

    idx = reg.load_catalog(tmp_path, config.repositories[0])
    assert set(idx.repositories) == {"toolkit"}

    match = reg.find_repo(tmp_path, config, "toolkit")
    assert match is not None and match[2].summary == "a toolkit"
    assert reg.find_repo(tmp_path, config, "nope") is None

    listed = {name for _, name, _ in reg.list_repos(tmp_path, config)}
    assert listed == {"toolkit"}


def test_add_whole_repo_installs_skill_and_mcp(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    cat = _catalog(tmp_path, _repo_with_skill_and_mcp(tmp_path))
    monkeypatch.chdir(project)

    assert runner.invoke(app, ["repo", "add", "c", str(cat)]).exit_code == 0
    res = runner.invoke(app, ["add", "toolkit"])
    assert res.exit_code == 0, res.output

    # Skill symlinked in...
    assert (project / ".claude/skills/reviewer").is_symlink()
    # ...and the MCP server merged — proving a catalog can carry MCP (merge ban lifted).
    mcp = json.loads((project / ".mcp.json").read_text())
    assert "weather" in mcp["mcpServers"]

    cfg = ConfigStore.load(project).parsed()
    assert cfg.source("toolkit") is not None
    assert cfg.find_component("toolkit/skill/reviewer") is not None
    assert cfg.find_component("toolkit/mcp/mcp") is not None


def test_add_repo_with_expose_mcp_only(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    cat = _catalog(
        tmp_path,
        _repo_with_skill_and_mcp(tmp_path),
        expose=[{"type": "mcp", "name": "mcp"}],
    )
    monkeypatch.chdir(project)
    runner.invoke(app, ["repo", "add", "c", str(cat)])
    res = runner.invoke(app, ["add", "toolkit"])
    assert res.exit_code == 0, res.output

    # Only the exposed MCP installed; the skill was not.
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"].keys() == {"weather"}
    assert not (project / ".claude/skills/reviewer").exists()
    cfg = ConfigStore.load(project).parsed()
    assert cfg.find_component("toolkit/mcp/mcp") is not None
    assert cfg.find_component("toolkit/skill/reviewer") is None


def test_bare_name_falls_back_to_skills_registry(tmp_path, monkeypatch):
    # No catalogs, but a skills registry lists the name → old behavior still works.
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    skill_repo = tmp_path / "cool"
    skill_repo.mkdir()
    (skill_repo / "SKILL.md").write_text("# cool\n")
    index = {
        "version": 1,
        "skills": {"cool": {"source": {"type": "local", "path": str(skill_repo)}, "install": "link", "path": "."}},
    }
    index_path = tmp_path / "skills.json"
    index_path.write_text(json.dumps(index))
    monkeypatch.chdir(project)
    runner.invoke(app, ["registry", "add", "r", str(index_path)])
    res = runner.invoke(app, ["add", "cool"])
    assert res.exit_code == 0, res.output
    assert (project / ".claude/skills/cool").is_symlink()


def test_invalid_catalog_errors(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    config = Config(repositories=[Registry(name="c", location=str(bad))])
    with pytest.raises(reg.RegistryError, match="invalid index"):
        reg.load_catalog(tmp_path, config.repositories[0])


def test_catalog_persisted_and_listed(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    cat = _catalog(tmp_path, _repo_with_skill_and_mcp(tmp_path))
    monkeypatch.chdir(project)
    runner.invoke(app, ["repo", "add", "c", str(cat)])

    cfg = ConfigStore.load(project).parsed()
    assert cfg.repositories and cfg.repositories[0].location == str(cat)
    out = runner.invoke(app, ["repo", "list"]).output
    assert "toolkit" in out and "whole repo" in out


def test_shipped_repositories_catalog_is_valid():
    from agentry.models import RepositoryIndex

    path = Path(__file__).resolve().parent.parent / "registry" / "repositories.json"
    idx = RepositoryIndex.model_validate(json.loads(path.read_text()))
    assert "arckit" in idx.repositories
    ark = idx.repositories["arckit"]
    assert ark.source.url == "https://github.com/tractorjuice/arc-kit"
    assert ark.source.subdir == "plugins/arckit-claude"
