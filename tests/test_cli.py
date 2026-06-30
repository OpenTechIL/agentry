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


def _write_overlay_catalog(path):
    import json

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": {},
                "targets": {
                    "myide": {"skill": {"strategy": "link", "dest": ".myide/skills/{name}"}}
                },
            }
        )
    )


def test_target_add_installs_overlay_and_resolves_target(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["myide"]).save()  # an otherwise-unresolved target
    make_source(tmp_path / "team")
    catalog = tmp_path / "overlays.json"
    _write_overlay_catalog(catalog)
    monkeypatch.chdir(project)
    runner.invoke(app, ["catalog", "add", "ov", str(catalog)])
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])
    runner.invoke(app, ["add", "team/skill/code-reviewer"])

    # Before the overlay, the unresolved target warns and nothing lands under .myide.
    assert not (project / ".myide/skills/code-reviewer").exists()

    out = runner.invoke(app, ["target", "add", "myide"]).output
    assert "myide" in out
    # The overlay is now in config and the skill installs to the overlay's destination.
    assert "myide" in ConfigStore.load(project).parsed().target_profiles
    assert (project / ".myide/skills/code-reviewer").is_symlink()


def test_target_add_unknown_errors(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    catalog = tmp_path / "overlays.json"
    _write_overlay_catalog(catalog)
    monkeypatch.chdir(project)
    runner.invoke(app, ["catalog", "add", "ov", str(catalog)])
    result = runner.invoke(app, ["target", "add", "ghostide"])
    assert result.exit_code == 1
    assert "No driver overlay named 'ghostide'" in result.output


def test_target_list_shows_status_and_available(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude", "myide"]).save()
    catalog = tmp_path / "overlays.json"
    _write_overlay_catalog(catalog)
    monkeypatch.chdir(project)
    runner.invoke(app, ["catalog", "add", "ov", str(catalog)])
    out = runner.invoke(app, ["target", "list"]).output
    assert "claude" in out and "resolved" in out
    # myide is active but unresolved, yet an overlay is available to fix it.
    assert "myide" in out and "overlay available" in out


def test_import_apm_creates_config_and_mcp_fragments(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    # A real apm.yml: one git skill dep + one inline MCP server.
    (project / "apm.yml").write_text(
        "name: demo\n"
        "version: 1.0.0\n"
        "targets:\n"
        "  - claude\n"
        "dependencies:\n"
        "  apm:\n"
        "    - github/awesome-copilot/skills/review-and-refactor\n"
        "  mcp:\n"
        "    - name: github\n"
        "      transport: stdio\n"
        "      command: npx\n"
        "      args: ['-y', '@modelcontextprotocol/server-github']\n"
    )
    monkeypatch.chdir(project)

    out = runner.invoke(app, ["import", "apm"]).output
    assert "Imported" in out

    cfg = ConfigStore.load(project).parsed()
    src_names = {s.name for s in cfg.sources}
    assert "awesome-copilot" in src_names  # git dep -> source
    assert "apm-import" in src_names  # inline MCP -> local fragment source
    refs = {c.ref for c in cfg.components}
    assert "awesome-copilot/skill/review-and-refactor" in refs
    assert "apm-import/mcp/github" in refs

    # The MCP fragment was written in agentry's merge shape.
    import json

    frag = json.loads((project / "apm-import" / "mcp" / "github.json").read_text())
    assert frag == {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}
    }


def test_import_apm_dry_run_writes_nothing(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "apm.yml").write_text(
        "name: demo\nversion: 1.0.0\ndependencies:\n"
        "  apm: ['github/acme/repo/skills/x']\n  mcp: []\n"
    )
    monkeypatch.chdir(project)
    out = runner.invoke(app, ["import", "apm", "--dry-run"]).output
    assert "source" in out and "nothing written" in out
    assert not ConfigStore.exists(project)


def test_import_apm_missing_file_errors(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["import", "apm"])
    assert result.exit_code == 1
    assert "No apm manifest" in result.output
