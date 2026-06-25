from __future__ import annotations

from pathlib import Path

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
