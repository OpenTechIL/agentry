from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry import registry as reg
from agentry.cli import app
from agentry.config import ConfigStore
from agentry.models import (
    Component,
    ComponentType,
    Config,
    ProfileRule,
    Registry,
    RegistrySource,
    RepositoryEntry,
    Strategy,
)

runner = CliRunner()


def _skill_repo(tmp_path: Path) -> Path:
    """A repo whose skill lives at a non-conventional path (needs an `expose` entry)."""
    repo = tmp_path / "cool"
    (repo / ".claude" / "skills" / "cool").mkdir(parents=True)
    (repo / ".claude" / "skills" / "cool" / "SKILL.md").write_text("# cool\n")
    return repo


def _write_catalog(tmp_path: Path, skill_repo: Path) -> Path:
    """A catalog with two exposed skills: one linked by path, one self-installing via generate."""
    script = (
        "import os;p=os.path.join(os.getcwd(), '.claude/skills/fake');"
        "os.makedirs(p, exist_ok=True);"
        "open(os.path.join(p,'SKILL.md'),'w').write('# fake\\n')"
    )
    catalog = {
        "version": 1,
        "repositories": {
            "cool": {
                "summary": "a cool skill",
                "source": {"type": "local", "path": str(skill_repo)},
                "expose": [
                    {"type": "skill", "name": "cool", "path": ".claude/skills/cool"},
                ],
            },
            "fake": {
                "summary": "self-installer",
                "source": {"type": "local", "path": str(skill_repo)},
                "expose": [
                    {
                        "type": "skill",
                        "name": "fake",
                        "generate": {
                            "command": [sys.executable, "-c", script],
                            "produces": [".claude/skills/fake"],
                        },
                    }
                ],
            },
        },
    }
    path = tmp_path / "repositories.json"
    path.write_text(json.dumps(catalog))
    return path


_C = ComponentType


def _comps(*ctypes: ComponentType) -> list[Component]:
    return [Component(source="arckit", type=t, name="x") for t in ctypes]


def test_build_install_profiles_copy_flips_strategy():
    entry = RepositoryEntry(source=RegistrySource(url="x"), copy=True, namespaced=False)
    profiles = reg.build_install_profiles(entry, "arckit", _comps(_C.COMMAND, _C.SKILL), {"claude"})
    cmd = profiles["claude"][_C.COMMAND]
    assert cmd.strategy is Strategy.COPY and cmd.dest == ".claude/commands/{name}.md"
    assert profiles["claude"][_C.SKILL].strategy is Strategy.COPY
    assert profiles["claude"][_C.SKILL].dest == ".claude/skills/{name}"


def test_build_install_profiles_namespaces_command_and_agent_only():
    entry = RepositoryEntry(source=RegistrySource(url="x"), copy=False, namespaced=True)
    profiles = reg.build_install_profiles(
        entry, "arckit", _comps(_C.COMMAND, _C.AGENT, _C.SKILL), {"claude"}
    )
    claude = profiles["claude"]
    assert claude[_C.COMMAND].strategy is Strategy.LINK
    assert claude[_C.COMMAND].dest == ".claude/commands/arckit/{name}.md"
    assert claude[_C.AGENT].dest == ".claude/agents/arckit/{name}.md"
    # Skill stays flat → no synthesized rule (built-in link default applies).
    assert _C.SKILL not in claude


def test_build_install_profiles_copy_and_namespaced_combined():
    entry = RepositoryEntry(source=RegistrySource(url="x"), copy=True, namespaced=True)
    profiles = reg.build_install_profiles(entry, "myrepo", _comps(_C.COMMAND), {"claude"})
    cmd = profiles["claude"][_C.COMMAND]
    assert cmd.strategy is Strategy.COPY and cmd.dest == ".claude/commands/myrepo/{name}.md"


def test_build_install_profiles_noop_when_flags_off():
    entry = RepositoryEntry(source=RegistrySource(url="x"), copy=False, namespaced=False)
    profiles = reg.build_install_profiles(entry, "arckit", _comps(_C.COMMAND, _C.SKILL), {"claude"})
    assert profiles == {}


def test_build_install_profiles_preserves_explicit_rules():
    entry = RepositoryEntry(
        source=RegistrySource(url="x"),
        copy=False,
        namespaced=True,
        target_profiles={
            "claude": {
                _C.HOOK: ProfileRule(
                    strategy=Strategy.LINK_MERGE,
                    dest=".claude/hooks/{name}",
                    file=".claude/settings.json",
                    pointer="hooks",
                )
            }
        },
    )
    profiles = reg.build_install_profiles(entry, "arckit", _comps(_C.COMMAND, _C.HOOK), {"claude"})
    # The explicit hook rule survives untouched; the command gets namespaced.
    assert profiles["claude"][_C.HOOK].strategy is Strategy.LINK_MERGE
    assert profiles["claude"][_C.COMMAND].dest == ".claude/commands/arckit/{name}.md"


def test_load_catalog_and_find(tmp_path: Path):
    catalog_path = _write_catalog(tmp_path, _skill_repo(tmp_path))
    config = Config(repositories=[Registry(name="r", location=str(catalog_path))])

    idx = reg.load_catalog(tmp_path, config.repositories[0])
    assert set(idx.repositories) == {"cool", "fake"}

    match = reg.find_repo(tmp_path, config, "cool")
    assert match is not None and match[2].expose[0].path == ".claude/skills/cool"
    assert reg.find_repo(tmp_path, config, "nope") is None

    listed = {name for _, name, _ in reg.list_repos(tmp_path, config)}
    assert listed == {"cool", "fake"}


def test_invalid_catalog_errors(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    config = Config(repositories=[Registry(name="r", location=str(bad))])
    with pytest.raises(reg.RegistryError, match="invalid index"):
        reg.load_catalog(tmp_path, config.repositories[0])


def test_add_exposed_link_skill(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    catalog_path = _write_catalog(tmp_path, _skill_repo(tmp_path))
    monkeypatch.chdir(project)

    assert runner.invoke(app, ["catalog", "add", "r", str(catalog_path)]).exit_code == 0
    result = runner.invoke(app, ["add", "cool"])
    assert result.exit_code == 0, result.output

    link = project / ".claude/skills/cool"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_text() == "# cool\n"
    # Resolved into a real source + component in config.
    cfg = ConfigStore.load(project).parsed()
    assert cfg.source("cool") is not None
    assert cfg.find_component("cool/skill/cool").path == ".claude/skills/cool"


def test_add_exposed_generate_skill_gated(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    catalog_path = _write_catalog(tmp_path, _skill_repo(tmp_path))
    monkeypatch.chdir(project)
    runner.invoke(app, ["catalog", "add", "r", str(catalog_path)])

    # Without --allow-run: added but not executed.
    res = runner.invoke(app, ["add", "fake"])
    assert res.exit_code == 0
    assert not (project / ".claude/skills/fake").exists()
    assert "--allow-run" in res.output

    # With --allow-run on a later sync: executes.
    res2 = runner.invoke(app, ["sync", "--allow-run"])
    assert res2.exit_code == 0, res2.output
    assert (project / ".claude/skills/fake/SKILL.md").read_text() == "# fake\n"


def test_add_unknown_repo_errors(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    catalog_path = _write_catalog(tmp_path, _skill_repo(tmp_path))
    monkeypatch.chdir(project)
    runner.invoke(app, ["catalog", "add", "r", str(catalog_path)])
    res = runner.invoke(app, ["add", "ghost"])
    assert res.exit_code == 1
    assert "No catalog lists 'ghost'" in res.output


def test_add_bare_name_without_catalogs_errors(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    res = runner.invoke(app, ["add", "graphify"])
    assert res.exit_code == 1
    assert "No catalog lists 'graphify'" in res.output


def test_shipped_catalog_is_valid():
    # The starter catalog committed at registry/repositories.json must parse and expose the
    # curated repos with sane install methods.
    from agentry.models import RepositoryIndex

    path = Path(__file__).resolve().parent.parent / "registry" / "repositories.json"
    idx = RepositoryIndex.model_validate(json.loads(path.read_text()))
    assert {"arckit", "ui-ux-pro-max", "graphify"} <= set(idx.repositories)

    ui = idx.repositories["ui-ux-pro-max"].expose[0]
    assert ui.type is ComponentType.SKILL and ui.path == ".claude/skills/ui-ux-pro-max"
    gph = idx.repositories["graphify"].expose[0]
    assert gph.generate is not None and gph.generate.produces == [".claude/skills/graphify"]


def test_shipped_repositories_catalog_has_arckit_hook_profile():
    # The curated repositories.json must declare arckit's claude hook link+merge profile so
    # `agy add arckit` rewrites ${CLAUDE_PLUGIN_ROOT} instead of merging it in verbatim.
    from agentry.models import RepositoryIndex, Strategy

    path = Path(__file__).resolve().parent.parent / "registry" / "repositories.json"
    idx = RepositoryIndex.model_validate(json.loads(path.read_text()))
    arckit = idx.repositories["arckit"]
    rule = arckit.target_profiles["claude"][ComponentType.HOOK]
    assert rule.strategy is Strategy.LINK_MERGE
    assert rule.rewrite_from == "${CLAUDE_PLUGIN_ROOT}/hooks"
    assert rule.dest == ".claude/hooks/agentry/{repo}@{ref}/{name}"
    assert rule.rewrite_to == "${CLAUDE_PROJECT_DIR}/.claude/hooks/agentry/{repo}@{ref}/{name}"


# -- multi-component repo: install-time selection -------------------------


def _multi_repo(tmp_path: Path) -> Path:
    """A conventional-layout repo with one skill, one command, one agent (all discoverable)."""
    repo = tmp_path / "plugin"
    (repo / "skills" / "alpha").mkdir(parents=True)
    (repo / "skills" / "alpha" / "SKILL.md").write_text("# alpha\n")
    (repo / "commands").mkdir(parents=True)
    (repo / "commands" / "beta.md").write_text("# beta\n")
    (repo / "agents").mkdir(parents=True)
    (repo / "agents" / "gamma.md").write_text("# gamma\n")
    return repo


def _write_multi_catalog(tmp_path: Path, source: Path) -> Path:
    catalog = {
        "version": 1,
        "repositories": {"demo": {"source": {"type": "local", "path": str(source)}}},
    }
    path = tmp_path / "repositories.json"
    path.write_text(json.dumps(catalog))
    return path


def _setup_multi(tmp_path, monkeypatch) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    catalog = _write_multi_catalog(tmp_path, _multi_repo(tmp_path))
    monkeypatch.chdir(project)
    runner.invoke(app, ["catalog", "add", "c", str(catalog)])
    return project


def _enabled_refs(project: Path) -> set[str]:
    return {c.ref for c in ConfigStore.load(project).parsed().components if c.enabled}


def test_add_select_single_component(tmp_path, monkeypatch):
    project = _setup_multi(tmp_path, monkeypatch)
    res = runner.invoke(app, ["add", "demo@alpha"])
    assert res.exit_code == 0, res.output
    assert _enabled_refs(project) == {"demo/skill/alpha"}


def test_add_select_multiple_components(tmp_path, monkeypatch):
    project = _setup_multi(tmp_path, monkeypatch)
    res = runner.invoke(app, ["add", "demo@alpha,beta"])
    assert res.exit_code == 0, res.output
    assert _enabled_refs(project) == {"demo/skill/alpha", "demo/command/beta"}


def test_add_select_unknown_component_errors(tmp_path, monkeypatch):
    _setup_multi(tmp_path, monkeypatch)
    res = runner.invoke(app, ["add", "demo@ghost"])
    assert res.exit_code == 1
    assert "no component" in res.output.lower() and "ghost" in res.output


def test_add_type_filter(tmp_path, monkeypatch):
    project = _setup_multi(tmp_path, monkeypatch)
    res = runner.invoke(app, ["add", "demo", "--type", "skill"])
    assert res.exit_code == 0, res.output
    assert _enabled_refs(project) == {"demo/skill/alpha"}


def test_add_unknown_type_errors(tmp_path, monkeypatch):
    _setup_multi(tmp_path, monkeypatch)
    res = runner.invoke(app, ["add", "demo", "--type", "bogus"])
    assert res.exit_code == 1
    assert "Unknown type 'bogus'" in res.output


def test_add_bare_repo_no_tty_installs_all(tmp_path, monkeypatch):
    project = _setup_multi(tmp_path, monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    res = runner.invoke(app, ["add", "demo"])
    assert res.exit_code == 0, res.output
    assert _enabled_refs(project) == {"demo/skill/alpha", "demo/command/beta", "demo/agent/gamma"}


# -- catalog plumbing (unchanged behaviour) -------------------------------


def _plugin_repo(tmp_path: Path) -> Path:
    """A minimal local source with one discoverable component (so `agy add` succeeds)."""
    repo = tmp_path / "plugin"
    (repo / "skills" / "demo").mkdir(parents=True)
    (repo / "skills" / "demo" / "SKILL.md").write_text("# demo\n")
    return repo


def _write_hook_catalog(tmp_path: Path, source: Path) -> Path:
    catalog = {
        "version": 1,
        "repositories": {
            "demo": {
                "source": {"type": "local", "path": str(source)},
                "target_profiles": {
                    "claude": {
                        "hook": {
                            "strategy": "link+merge",
                            "dest": ".claude/hooks/{name}",
                            "file": ".claude/settings.json",
                            "pointer": "hooks",
                            "rewrite_from": "${CLAUDE_PLUGIN_ROOT}/hooks",
                            "rewrite_to": "${CLAUDE_PROJECT_DIR}/.claude/hooks/{name}",
                        }
                    }
                },
            }
        },
    }
    path = tmp_path / "repositories.json"
    path.write_text(json.dumps(catalog))
    return path


def test_add_catalog_repo_writes_target_profiles_idempotent(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    catalog = _write_hook_catalog(tmp_path, _plugin_repo(tmp_path))
    monkeypatch.chdir(project)
    runner.invoke(app, ["catalog", "add", "curated", str(catalog)])

    res = runner.invoke(app, ["add", "demo"])
    assert res.exit_code == 0, res.output
    cfg = ConfigStore.load(project).parsed()
    rule = cfg.target_profiles["claude"][ComponentType.HOOK]
    assert rule.rewrite_from == "${CLAUDE_PLUGIN_ROOT}/hooks"

    # A user customizes the written rule; re-adding the repo must not clobber it.
    store = ConfigStore.load(project)
    store.doc["target_profiles"]["claude"]["hook"]["rewrite_to"] = "${CLAUDE_PROJECT_DIR}/custom"
    store.save()
    res2 = runner.invoke(app, ["add", "demo"])
    assert res2.exit_code == 0, res2.output
    cfg2 = ConfigStore.load(project).parsed()
    assert (
        cfg2.target_profiles["claude"][ComponentType.HOOK].rewrite_to
        == "${CLAUDE_PROJECT_DIR}/custom"
    )


def test_normalize_github_web_url():
    # github.com blob/raw web URLs are rewritten to raw.githubusercontent.com so a URL
    # pasted from the browser actually serves the JSON.
    assert (
        reg._normalize_url("https://github.com/acme/cat/blob/main/registry/repositories.json")
        == "https://raw.githubusercontent.com/acme/cat/main/registry/repositories.json"
    )
    assert (
        reg._normalize_url("https://github.com/acme/cat/raw/v2/repositories.json")
        == "https://raw.githubusercontent.com/acme/cat/v2/repositories.json"
    )
    # Already-raw and non-GitHub URLs pass through untouched.
    raw = "https://raw.githubusercontent.com/acme/cat/main/repositories.json"
    assert reg._normalize_url(raw) == raw
    other = "https://example.com/repositories.json"
    assert reg._normalize_url(other) == other


def test_repo_catalog_persisted_and_listed_via_url(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    ConfigStore.create(project, ["claude"]).save()
    monkeypatch.chdir(project)
    url = "https://github.com/acme/cat/blob/main/repositories.json"
    runner.invoke(app, ["catalog", "add", "curated", url])

    cfg = ConfigStore.load(project).parsed()
    assert cfg.repositories and cfg.repositories[0].location == url


# -- `agy catalog add-repo` (catalog authoring) -------------------------------


def test_parse_repo_url_plain_and_tree():
    clean, ref, subdir, name = reg.parse_repo_url("https://github.com/acme/widget")
    assert (clean, ref, subdir, name) == ("https://github.com/acme/widget", None, None, "widget")

    clean, ref, subdir, name = reg.parse_repo_url(
        "https://github.com/acme/widget/tree/dev/plugins/x"
    )
    assert clean == "https://github.com/acme/widget"
    assert ref == "dev"
    assert subdir == "plugins/x"
    assert name == "widget"

    # Trailing .git is stripped from the derived name.
    _, _, _, name = reg.parse_repo_url("https://github.com/acme/widget.git")
    assert name == "widget"


def test_catalog_add_repo_minimal(tmp_path):
    catalog = tmp_path / "repositories.json"
    result = runner.invoke(
        app, ["catalog", "add-repo", "https://github.com/o/r", "cool", "--file", str(catalog)]
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(catalog.read_text())
    entry = doc["repositories"]["cool"]
    assert entry["source"] == {"type": "git", "url": "https://github.com/o/r", "ref": "main"}
    assert "expose" not in entry
    assert "target_profiles" not in entry
    assert "summary" not in entry


def test_catalog_add_repo_derives_name_and_infers_ref_subdir(tmp_path):
    catalog = tmp_path / "repositories.json"
    result = runner.invoke(
        app,
        [
            "catalog",
            "add-repo",
            "https://github.com/acme/widget/tree/dev/plugins/x",
            "--file",
            str(catalog),
        ],
    )
    assert result.exit_code == 0, result.output
    entry = json.loads(catalog.read_text())["repositories"]["widget"]
    assert entry["source"] == {
        "type": "git",
        "url": "https://github.com/acme/widget",
        "ref": "dev",
        "subdir": "plugins/x",
    }


def test_catalog_add_repo_summary_and_duplicate(tmp_path):
    catalog = tmp_path / "repositories.json"
    runner.invoke(
        app,
        [
            "catalog",
            "add-repo",
            "https://github.com/o/r",
            "cool",
            "--summary",
            "hi",
            "--file",
            str(catalog),
        ],
    )
    assert json.loads(catalog.read_text())["repositories"]["cool"]["summary"] == "hi"

    dup = runner.invoke(
        app, ["catalog", "add-repo", "https://github.com/o/r2", "cool", "--file", str(catalog)]
    )
    assert dup.exit_code == 1
    # The original entry is untouched (no partial overwrite).
    assert (
        json.loads(catalog.read_text())["repositories"]["cool"]["source"]["url"]
        == "https://github.com/o/r"
    )

    forced = runner.invoke(
        app,
        [
            "catalog",
            "add-repo",
            "https://github.com/o/r2",
            "cool",
            "--force",
            "--file",
            str(catalog),
        ],
    )
    assert forced.exit_code == 0
    assert (
        json.loads(catalog.read_text())["repositories"]["cool"]["source"]["url"]
        == "https://github.com/o/r2"
    )


def test_catalog_add_repo_discover(tmp_path, monkeypatch, git_source):
    # Re-init the fixture repo on an explicit `main` branch so --ref main checks out.
    import subprocess

    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    subprocess.run(
        ["git", "branch", "-m", "main"], cwd=git_source, check=True, env={**os.environ, **env}
    )

    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    catalog = workdir / "repositories.json"
    result = runner.invoke(
        app,
        [
            "catalog",
            "add-repo",
            f"file://{git_source}",
            "demo",
            "--discover",
            "--file",
            str(catalog),
        ],
    )
    assert result.exit_code == 0, result.output
    expose = json.loads(catalog.read_text())["repositories"]["demo"]["expose"]
    pairs = {(e["type"], e["name"]) for e in expose}
    assert ("skill", "code-reviewer") in pairs
    assert ("agent", "planner") in pairs
    assert ("mcp", "github") in pairs
