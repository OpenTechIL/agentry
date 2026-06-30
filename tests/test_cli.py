from __future__ import annotations

from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from conftest import make_source

runner = CliRunner()


def test_list_groups_by_source(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    # Two sources so grouping is observable.
    make_source(tmp_path / "alpha")
    make_source(tmp_path / "beta")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "alpha", str(tmp_path / "alpha"), "--local"])
    runner.invoke(app, ["source", "add", "beta", str(tmp_path / "beta"), "--local"])

    out = runner.invoke(app, ["list"]).output
    # Each source gets its own titled table with a component count.
    assert "alpha" in out and "beta" in out
    assert "6 components" in out
    # Grouped view shows type + name columns (not long refs).
    assert "code-reviewer" in out and "skill" in out


def test_list_empty_message(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    out = runner.invoke(app, ["list"]).output
    assert "No components found" in out


def test_why_shows_provenance_and_install_targets(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    make_source(tmp_path / "team")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])
    runner.invoke(app, ["add", "team/skill/code-reviewer"])

    out = runner.invoke(app, ["why", "team/skill/code-reviewer"]).output
    assert "team/skill/code-reviewer" in out
    assert "source:" in out and "team" in out
    assert ".claude/skills/code-reviewer" in out  # resolved install target
    assert "ok" in out


def test_why_errors_on_unknown_ref(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["why", "team/skill/nope"])
    assert result.exit_code == 1
    assert "No such component" in result.output
