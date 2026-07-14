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


def test_source_add_prints_provenance(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    make_source(tmp_path / "team")
    monkeypatch.chdir(project)
    out = runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"]).output
    assert "provenance:" in out  # origin shown at first install
    assert "team" in out and "local" in out


def test_trust_command_records_consent(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    make_source(tmp_path / "team")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])

    res = runner.invoke(app, ["trust", "team"])
    assert res.exit_code == 0 and "Trusted source" in res.output
    from agentry.lockfile import load_lock

    assert load_lock(project).entry("team").trusted is True
    # Idempotent: trusting again reports already-trusted.
    again = runner.invoke(app, ["trust", "team"])
    assert "already trusted" in again.output


def test_trust_unknown_source_errors(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    res = runner.invoke(app, ["trust", "ghost"])
    assert res.exit_code == 1


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


def test_emit_agents_md_writes_and_checks(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    make_source(tmp_path / "team")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])
    runner.invoke(app, ["add", "team/skill/code-reviewer"])
    runner.invoke(app, ["add", "team/agent/planner"])

    out = runner.invoke(app, ["emit", "agents-md"]).output
    assert "Wrote" in out
    agents_md = (project / "AGENTS.md").read_text()
    assert "# AGENTS.md" in agents_md
    assert "## code-reviewer (skill)" in agents_md and "# code reviewer" in agents_md
    assert "## planner (agent)" in agents_md

    # Deterministic + committed: --check passes right after a write.
    assert runner.invoke(app, ["emit", "agents-md", "--check"]).exit_code == 0

    # A source change makes the committed file stale → --check fails (the CI verify path).
    (tmp_path / "team" / "skills" / "code-reviewer" / "SKILL.md").write_text("# changed\n")
    result = runner.invoke(app, ["emit", "agents-md", "--check"])
    assert result.exit_code == 1
    assert "out of date" in result.output


def test_emit_agents_md_no_components(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    out = runner.invoke(app, ["emit", "agents-md"]).output
    assert "No skill/agent/command" in out
    assert not (project / "AGENTS.md").exists()


def _described_skill_source(root, name="greeter", desc="Use when the user says hello."):
    d = root / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# body\n")
    return root


def test_emit_triggers_fans_out_to_target_memory_files(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude", "opencode"]).save()
    _described_skill_source(tmp_path / "team")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])
    runner.invoke(app, ["add", "team/skill/greeter"])

    res = runner.invoke(app, ["emit", "triggers"])
    assert res.exit_code == 0
    # claude -> .claude/CLAUDE.md, opencode -> AGENTS.md
    for path in (project / ".claude" / "CLAUDE.md", project / "AGENTS.md"):
        doc = path.read_text()
        assert "## Agentry-managed skills" in doc
        assert "- **greeter** — Use when the user says hello." in doc

    # Idempotent: --check passes right after a write.
    assert runner.invoke(app, ["emit", "triggers", "--check"]).exit_code == 0

    # A description change makes memory files stale → --check fails (the CI verify path).
    (tmp_path / "team" / "skills" / "greeter" / "SKILL.md").write_text(
        "---\nname: greeter\ndescription: Use when the user waves goodbye.\n---\n# body\n"
    )
    stale = runner.invoke(app, ["emit", "triggers", "--check"])
    assert stale.exit_code == 1
    assert "Out of date" in stale.output


def test_emit_triggers_output_override_single_file(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    _described_skill_source(tmp_path / "team")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])
    runner.invoke(app, ["add", "team/skill/greeter"])

    res = runner.invoke(app, ["emit", "triggers", "-o", "NOTES.md"])
    assert res.exit_code == 0
    assert "- **greeter** — Use when the user says hello." in (project / "NOTES.md").read_text()
    # Fan-out target was NOT written when -o is given.
    assert not (project / ".claude" / "CLAUDE.md").exists()


def test_emit_triggers_preserves_hand_authored_prose(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    _described_skill_source(tmp_path / "team")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])
    runner.invoke(app, ["add", "team/skill/greeter"])

    mem = project / ".claude" / "CLAUDE.md"
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text("# Project rules\n\nAlways run tests.\n")

    runner.invoke(app, ["emit", "triggers"])
    doc = mem.read_text()
    assert "# Project rules" in doc and "Always run tests." in doc  # hand prose survives
    assert "## Agentry-managed skills" in doc

    # A second run is byte-identical (idempotent, no churn).
    before = mem.read_text()
    runner.invoke(app, ["emit", "triggers"])
    assert mem.read_text() == before


def test_emit_triggers_no_skills(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    out = runner.invoke(app, ["emit", "triggers"]).output
    assert "No skill components" in out
    assert not (project / ".claude" / "CLAUDE.md").exists()


def _project_with_component(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    make_source(tmp_path / "team")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])
    runner.invoke(app, ["add", "team/skill/code-reviewer"])
    return project


def test_emit_agent_requires_allow_transform(tmp_path, monkeypatch):
    project = _project_with_component(tmp_path, monkeypatch)
    store = ConfigStore.load(project)
    store.doc["transform"] = {"command": ["cat"]}
    store.save()
    result = runner.invoke(app, ["emit", "agents-md", "--agent"])
    assert result.exit_code == 1
    assert "allow-transform" in result.output


def test_emit_agent_requires_configured_command(tmp_path, monkeypatch):
    _project_with_component(tmp_path, monkeypatch)  # no transform.command in config
    result = runner.invoke(app, ["emit", "agents-md", "--agent", "--allow-transform"])
    assert result.exit_code == 1
    assert "No transform command" in result.output


def test_emit_agent_synthesizes_and_writes(tmp_path, monkeypatch):
    project = _project_with_component(tmp_path, monkeypatch)
    store = ConfigStore.load(project)
    store.doc["transform"] = {"command": ["fake-agent"]}
    store.save()

    import agentry.emit as emit_mod

    monkeypatch.setattr(emit_mod, "run_agent", lambda cmd, prompt: "# AGENTS.md\n\nSynthesized.\n")
    # --yes skips the confirmation prompt (the CI auto-apply path).
    out = runner.invoke(app, ["emit", "agents-md", "--agent", "--allow-transform", "--yes"]).output
    assert "Wrote" in out
    assert (project / "AGENTS.md").read_text() == "# AGENTS.md\n\nSynthesized.\n"


def test_emit_agent_rejects_check(tmp_path, monkeypatch):
    project = _project_with_component(tmp_path, monkeypatch)
    store = ConfigStore.load(project)
    store.doc["transform"] = {"command": ["cat"]}
    store.save()
    result = runner.invoke(app, ["emit", "agents-md", "--agent", "--allow-transform", "--check"])
    assert result.exit_code == 1
    assert "reproducible" in result.output


def test_doctor_clean_project_exits_zero(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    make_source(tmp_path / "team")
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(tmp_path / "team"), "--local"])
    runner.invoke(app, ["add", "team/skill/code-reviewer"])
    runner.invoke(app, ["sync"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0


def test_doctor_errors_on_undefined_target(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    store = ConfigStore.load(project)
    store.doc["targets"].append("ghostide")
    store.save()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "ghostide" in result.output


def test_doctor_strict_fails_on_warnings(tmp_path, monkeypatch):
    import json

    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    src = tmp_path / "team"
    (src / "mcp").mkdir(parents=True)
    # An MCP server referencing an unset env var → a doctor warning (not an error).
    (src / "mcp" / "gh.json").write_text(
        json.dumps({"gh": {"command": "x", "env": {"T": "${UNSET_STRICT_VAR}"}}})
    )
    monkeypatch.chdir(project)
    runner.invoke(app, ["source", "add", "team", str(src), "--local"])
    runner.invoke(app, ["add", "team/mcp/gh"])
    assert runner.invoke(app, ["doctor"]).exit_code == 0  # warnings don't fail by default
    assert runner.invoke(app, ["doctor", "--strict"]).exit_code == 1
