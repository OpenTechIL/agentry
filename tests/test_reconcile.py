from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from agentry.config import ConfigStore
from agentry.lockfile import load_lock
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync


def _wire(project: Path, source: Path, *comps: tuple[ComponentType, str]) -> None:
    store = ConfigStore.load(project)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(source)))
    for ctype, name in comps:
        store.add_component(Component(source="s", type=ctype, name=name, enabled=True))
    store.save()


def test_link_install_and_idempotent(project: Path, local_source: Path):
    _wire(project, local_source, (ComponentType.SKILL, "code-reviewer"))
    res = sync(project)
    link = project / ".claude/skills/code-reviewer"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_text() == "# code reviewer\n"
    assert any("link .claude/skills/code-reviewer" in c for c in res.created)

    res2 = sync(project)
    assert res2.created == [] and res2.updated == [] and res2.removed == []


def test_merge_install_and_reversible(project: Path, local_source: Path):
    _wire(project, local_source, (ComponentType.MCP, "github"))
    sync(project)
    mcp = json.loads((project / ".mcp.json").read_text())
    assert "github" in mcp["mcpServers"]

    # hand-add an entry that agentry must never touch
    mcp["mcpServers"]["hand-added"] = {"command": "x"}
    (project / ".mcp.json").write_text(json.dumps(mcp))

    store = ConfigStore.load(project)
    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(project)

    after = json.loads((project / ".mcp.json").read_text())
    assert "github" not in after["mcpServers"]
    assert "hand-added" in after["mcpServers"]


def test_root_mcp_json_merges_without_path(project: Path, tmp_path: Path):
    # A plugin-style source whose MCP is a single root `.mcp.json` (not mcp/<name>.json).
    # Discovery surfaces it as `s/mcp/mcp`, no --path needed.
    src = tmp_path / "plugin"
    src.mkdir()
    (src / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "weather": {"type": "http", "url": "https://example.com/mcp"},
                    "docs": {"type": "http", "url": "https://example.com/docs"},
                }
            }
        )
    )
    _wire(project, src, (ComponentType.MCP, "mcp"))
    res = sync(project)
    assert not res.warnings
    mcp = json.loads((project / ".mcp.json").read_text())
    assert mcp["mcpServers"].keys() == {"weather", "docs"}

    rows, _ = status(project)
    assert all(r.state == "ok" for r in rows)

    # Reversible: disabling removes exactly those servers.
    store = ConfigStore.load(project)
    store.set_enabled("s/mcp/mcp", False)
    store.save()
    sync(project)
    after = json.loads((project / ".mcp.json").read_text())
    assert "mcpServers" not in after or not after["mcpServers"]


def test_disable_removes_link_keeps_config(project: Path, local_source: Path):
    _wire(project, local_source, (ComponentType.SKILL, "code-reviewer"))
    sync(project)
    store = ConfigStore.load(project)
    store.set_enabled("s/skill/code-reviewer", False)
    store.save()
    sync(project)
    assert not (project / ".claude/skills/code-reviewer").exists()
    assert ConfigStore.load(project).parsed().find_component("s/skill/code-reviewer") is not None


def test_safety_unmanaged_file_untouched(project: Path, local_source: Path):
    # A user-owned file sitting where a component would link.
    (project / ".claude/skills").mkdir(parents=True)
    own = project / ".claude/skills/mine"
    own.write_text("mine")
    _wire(project, local_source, (ComponentType.SKILL, "code-reviewer"))
    sync(project)
    assert own.read_text() == "mine"  # never removed
    assert (project / ".claude/skills/code-reviewer").is_symlink()


def test_determinism_resync_from_lock(project: Path, local_source: Path):
    _wire(project, local_source, (ComponentType.SKILL, "code-reviewer"))
    sync(project)
    locked = load_lock(project).entry("s").resolved

    shutil.rmtree(project / ".agentry")
    sync(project)
    assert load_lock(project).entry("s").resolved == locked
    assert (project / ".claude/skills/code-reviewer/SKILL.md").read_text() == "# code reviewer\n"


def test_status_reports_drift(project: Path, local_source: Path):
    _wire(project, local_source, (ComponentType.SKILL, "code-reviewer"))
    sync(project)
    rows, _ = status(project)
    assert all(r.state == "ok" for r in rows)

    (project / ".claude/skills/code-reviewer").unlink()
    rows, _ = status(project)
    assert any(r.state == "missing" for r in rows)


def test_subdir_source_discovers_and_installs(project: Path, nested_source: Path):
    # Components live under plugins/pack — root scan finds nothing, subdir finds them.
    store = ConfigStore.load(project)
    store.add_source(
        Source(name="s", type=SourceType.LOCAL, path=str(nested_source), subdir="plugins/pack")
    )
    store.add_component(Component(source="s", type=ComponentType.SKILL, name="code-reviewer"))
    store.save()
    res = sync(project)
    link = project / ".claude/skills/code-reviewer"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_text() == "# code reviewer\n"
    assert not res.warnings
    rows, _ = status(project)
    assert all(r.state == "ok" for r in rows)


def test_subdir_persisted_in_config(project: Path, nested_source: Path):
    store = ConfigStore.load(project)
    store.add_source(
        Source(name="s", type=SourceType.LOCAL, path=str(nested_source), subdir="plugins/pack")
    )
    store.save()
    assert ConfigStore.load(project).parsed().source("s").subdir == "plugins/pack"


def test_subdir_must_be_relative_inside_repo():
    import pytest

    with pytest.raises(ValueError, match="relative path inside the repo"):
        Source(name="s", type=SourceType.LOCAL, path="/x", subdir="../escape")


def test_component_path_must_be_relative_inside_source():
    import pytest

    with pytest.raises(ValueError, match="relative path inside the project"):
        Component(source="s", type=ComponentType.SKILL, name="x", path="../escape")


def _generator_component(produces: str = ".claude/skills/fake/SKILL.md") -> "GeneratorSpec":
    from agentry.models import GeneratorSpec

    # A fake "graphify": writes a skill file into the project on its own.
    script = (
        "import os,sys;"
        f"p=os.path.join(os.getcwd(), {os.path.dirname(produces)!r});"
        "os.makedirs(p, exist_ok=True);"
        f"open(os.path.join(os.getcwd(), {produces!r}),'w').write('# fake skill\\n')"
    )
    return GeneratorSpec(command=[sys.executable, "-c", script], produces=[os.path.dirname(produces)])


def _wire_generator(project: Path, source: Path, spec) -> None:
    store = ConfigStore.load(project)
    store.add_source(Source(name="g", type=SourceType.LOCAL, path=str(source)))
    store.add_component(Component(source="g", type=ComponentType.SKILL, name="fake", generate=spec))
    store.save()


def test_generate_requires_allow_run(project: Path, local_source: Path):
    _wire_generator(project, local_source, _generator_component())
    res = sync(project)  # no allow_run
    assert not (project / ".claude/skills/fake").exists()
    assert any("--allow-run" in w for w in res.warnings)


def test_generate_runs_and_tracks_and_removes(project: Path, local_source: Path):
    spec = _generator_component()
    _wire_generator(project, local_source, spec)
    res = sync(project, allow_run=True)
    produced = project / ".claude/skills/fake"
    assert (produced / "SKILL.md").read_text() == "# fake skill\n"
    assert any("generated g/skill/fake" in c for c in res.created)

    # Manifest tracks the produced path.
    from agentry.manifest import load_manifest

    man = load_manifest(project)
    assert man.generated and man.generated[0].paths == [".claude/skills/fake"]

    # Idempotent: outputs present + tracked → no re-run, no changes.
    res2 = sync(project, allow_run=True)
    assert res2.created == [] and res2.updated == [] and res2.removed == []

    # Removal deletes exactly the produced path (no --allow-run needed to uninstall).
    store = ConfigStore.load(project)
    store.remove_component("g/skill/fake")
    store.save()
    res3 = sync(project)
    assert not produced.exists()
    assert any("generated .claude/skills/fake" in r for r in res3.removed)


def test_generate_status_reports_state(project: Path, local_source: Path):
    _wire_generator(project, local_source, _generator_component())
    sync(project, allow_run=True)
    rows, _ = status(project)
    assert any(r.ref == "g/skill/fake" and r.state == "ok" for r in rows)

    shutil.rmtree(project / ".claude/skills/fake")
    rows, _ = status(project)
    assert any(r.ref == "g/skill/fake" and r.state == "missing" for r in rows)


def test_generate_persisted_in_config(project: Path, local_source: Path):
    _wire_generator(project, local_source, _generator_component())
    spec = ConfigStore.load(project).parsed().find_component("g/skill/fake").generate
    assert spec.produces == [".claude/skills/fake"]
    assert spec.command[0] == sys.executable


def test_generate_produces_required():
    import pytest

    from agentry.models import GeneratorSpec

    with pytest.raises(ValueError, match="produces"):
        GeneratorSpec(command=["echo", "hi"], produces=[])


def test_generate_and_path_mutually_exclusive():
    import pytest

    from agentry.models import GeneratorSpec

    with pytest.raises(ValueError, match="either 'path' or 'generate'"):
        Component(
            source="s", type=ComponentType.SKILL, name="x", path=".",
            generate=GeneratorSpec(command=["echo"], produces=["out"]),
        )


def test_select_entries_unwraps_and_passes_through():
    from agentry.installers import merge
    from agentry.targets import MergeDest

    hooks_dest = MergeDest(".claude/settings.json", "hooks")
    # Claude-plugin-shaped hooks file: entries wrapped under "hooks", plus metadata.
    wrapped = {"description": "meta", "hooks": {"Stop": [{"matcher": ".*"}]}}
    assert merge.select_entries(wrapped, hooks_dest) == {"Stop": [{"matcher": ".*"}]}

    # An already-flat fragment is returned unchanged.
    flat = {"Stop": [{"matcher": ".*"}]}
    assert merge.select_entries(flat, hooks_dest) == flat

    # Alias unwrap: an OpenCode mcp dest also accepts the Claude-style wrapper.
    oc_dest = MergeDest("opencode.json", "mcp", aliases=("mcpServers",))
    assert merge.select_entries({"mcpServers": {"gh": {"x": 1}}}, oc_dest) == {"gh": {"x": 1}}


def test_wrapped_hooks_fragment_installs_flat(project: Path, tmp_path: Path):
    # A source shipping a Claude-Code-plugin-shaped hooks.json (wrapped under "hooks"
    # with a sibling "description") must merge as flat event entries — not double-nested.
    src = tmp_path / "wrapsrc"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "description": "plugin metadata that is not a hook entry",
                "hooks": {"Stop": [{"matcher": ".*", "hooks": [{"type": "command", "command": "node"}]}]},
            }
        )
    )
    _wire(project, src, (ComponentType.HOOK, "hooks"))
    sync(project)

    settings = json.loads((project / ".claude/settings.json").read_text())
    assert "Stop" in settings["hooks"]  # event entry merged directly
    assert "hooks" not in settings["hooks"]  # not double-nested
    assert "description" not in settings["hooks"]  # metadata dropped

    rows, _ = status(project)
    assert all(r.state == "ok" for r in rows)

    # Reversible: disabling removes the event key cleanly.
    store = ConfigStore.load(project)
    store.set_enabled("s/hook/hooks", False)
    store.save()
    sync(project)
    after = json.loads((project / ".claude/settings.json").read_text())
    assert "hooks" not in after or "Stop" not in after.get("hooks", {})


def _hooks_source(tmp_path: Path) -> Path:
    """A plugin-style source: a hooks/ dir of scripts + a plugin-shaped hooks.json."""
    src = tmp_path / "hooksrc"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "graph.mjs").write_text("export default () => {}\n")
    (src / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "description": "plugin metadata",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": ".*",
                            "hooks": [
                                {"type": "command", "command": "node ${CLAUDE_PLUGIN_ROOT}/hooks/graph.mjs"}
                            ],
                        }
                    ]
                },
            }
        )
    )
    return src


def _wire_link_merge_hooks(project: Path, source: Path) -> None:
    """Configure a claude HOOK link+merge profile + the hooks dir component (path='hooks')."""
    store = ConfigStore.load(project)
    store.doc["target_profiles"] = {
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
    }
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(source)))
    store.add_component(Component(source="s", type=ComponentType.HOOK, name="hooks", path="hooks"))
    store.save()


def test_link_merge_installs_dir_and_rewrites_commands(project: Path, tmp_path: Path):
    _wire_link_merge_hooks(project, _hooks_source(tmp_path))
    res = sync(project)
    assert not res.warnings

    # The script dir is symlinked in and resolves.
    link = project / ".claude/hooks/hooks"
    assert link.is_symlink()
    assert (link / "graph.mjs").read_text() == "export default () => {}\n"

    # The hooks merged flat under "hooks", with the command path rewritten.
    settings = json.loads((project / ".claude/settings.json").read_text())
    cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert cmd == "node ${CLAUDE_PROJECT_DIR}/.claude/hooks/hooks/graph.mjs"
    assert "${CLAUDE_PLUGIN_ROOT}" not in cmd

    rows, _ = status(project)
    assert all(r.state == "ok" for r in rows)


def test_link_merge_idempotent(project: Path, tmp_path: Path):
    _wire_link_merge_hooks(project, _hooks_source(tmp_path))
    sync(project)
    res2 = sync(project)
    assert res2.created == [] and res2.updated == [] and res2.removed == []


def test_link_merge_reversible(project: Path, tmp_path: Path):
    _wire_link_merge_hooks(project, _hooks_source(tmp_path))
    sync(project)

    # Hand-add an entry agentry must not touch.
    settings = json.loads((project / ".claude/settings.json").read_text())
    settings["hooks"]["Stop"] = [{"matcher": "x"}]
    (project / ".claude/settings.json").write_text(json.dumps(settings))

    store = ConfigStore.load(project)
    store.set_enabled("s/hook/hooks", False)
    store.save()
    sync(project)

    assert not (project / ".claude/hooks/hooks").exists()  # symlink gone
    after = json.loads((project / ".claude/settings.json").read_text())
    assert "PreToolUse" not in after.get("hooks", {})  # our key removed
    assert after["hooks"]["Stop"] == [{"matcher": "x"}]  # hand-added kept


def test_link_merge_namespaces_dest_by_repo_and_ref(project: Path, tmp_path: Path):
    # A profile dest with {repo}/{ref} placeholders namespaces the linked dir per source,
    # avoiding the {name}="hooks" collision and recording provenance in the path.
    source = _hooks_source(tmp_path)  # local source dir basename -> "hooksrc"
    store = ConfigStore.load(project)
    store.doc["target_profiles"] = {
        "claude": {
            "hook": {
                "strategy": "link+merge",
                "dest": ".claude/hooks/agentry/{repo}@{ref}/{name}",
                "file": ".claude/settings.json",
                "pointer": "hooks",
                "rewrite_from": "${CLAUDE_PLUGIN_ROOT}/hooks",
                "rewrite_to": "${CLAUDE_PROJECT_DIR}/.claude/hooks/agentry/{repo}@{ref}/{name}",
            }
        }
    }
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(source)))
    store.add_component(Component(source="s", type=ComponentType.HOOK, name="hooks", path="hooks"))
    store.save()

    res = sync(project)
    assert not res.warnings

    link = project / ".claude/hooks/agentry/hooksrc@main/hooks"
    assert link.is_symlink()
    assert (link / "graph.mjs").read_text() == "export default () => {}\n"

    settings = json.loads((project / ".claude/settings.json").read_text())
    cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert cmd == "node ${CLAUDE_PROJECT_DIR}/.claude/hooks/agentry/hooksrc@main/hooks/graph.mjs"

    # Disabling removes the symlink and prunes the now-empty namespace dirs.
    store = ConfigStore.load(project)
    store.set_enabled("s/hook/hooks", False)
    store.save()
    sync(project)
    assert not (project / ".claude/hooks/agentry/hooksrc@main/hooks").exists()
    assert not (project / ".claude/hooks/agentry").exists()  # pruned up the tree


def test_link_merge_dest_change_removes_old_link(project: Path, tmp_path: Path):
    # Changing the dest template between syncs must move the symlink, not orphan the old one.
    _wire_link_merge_hooks(project, _hooks_source(tmp_path))  # dest = .claude/hooks/{name}
    sync(project)
    assert (project / ".claude/hooks/hooks").is_symlink()

    store = ConfigStore.load(project)
    store.doc["target_profiles"]["claude"]["hook"]["dest"] = ".claude/hooks/agentry/{repo}@{ref}/{name}"
    store.doc["target_profiles"]["claude"]["hook"]["rewrite_to"] = (
        "${CLAUDE_PROJECT_DIR}/.claude/hooks/agentry/{repo}@{ref}/{name}"
    )
    store.save()
    sync(project)

    assert (project / ".claude/hooks/agentry/hooksrc@main/hooks").is_symlink()
    assert not (project / ".claude/hooks/hooks").exists()  # old link removed, not orphaned


def test_link_merge_warns_on_unrewritable_command(project: Path, tmp_path: Path):
    src = tmp_path / "hooksrc"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "node ${CLAUDE_PLUGIN_ROOT}/elsewhere/x.mjs"}]}
                    ]
                }
            }
        )
    )
    _wire_link_merge_hooks(project, src)
    res = sync(project)
    # rewrite_from is ".../hooks", so a ".../elsewhere/..." path stays unrewritten → warned.
    assert any("may not resolve" in w and "elsewhere" in w for w in res.warnings)


def test_explicit_path_root_is_skill(project: Path, tmp_path: Path):
    # A repo whose *root* is the skill (no skills/<name>/ wrapper, no descriptor).
    skill_repo = tmp_path / "cool-skill"
    skill_repo.mkdir()
    (skill_repo / "SKILL.md").write_text("# cool\n")
    store = ConfigStore.load(project)
    store.add_source(Source(name="cs", type=SourceType.LOCAL, path=str(skill_repo)))
    store.add_component(Component(source="cs", type=ComponentType.SKILL, name="cool-skill", path="."))
    store.save()

    res = sync(project)
    link = project / ".claude/skills/cool-skill"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_text() == "# cool\n"
    assert not res.warnings

    # Idempotent.
    res2 = sync(project)
    assert res2.created == [] and res2.updated == [] and res2.removed == []

    # Persisted in config.
    assert ConfigStore.load(project).parsed().find_component("cs/skill/cool-skill").path == "."

    # Reversible: removing the component cleanly unlinks and prunes.
    store = ConfigStore.load(project)
    store.remove_component("cs/skill/cool-skill")
    store.save()
    sync(project)
    assert not link.exists()


def test_explicit_path_subdir_artifact(project: Path, tmp_path: Path):
    # Skill at an arbitrary subpath, not under skills/.
    repo = tmp_path / "repo"
    (repo / "packages" / "my-skill").mkdir(parents=True)
    (repo / "packages" / "my-skill" / "SKILL.md").write_text("# mine\n")
    store = ConfigStore.load(project)
    store.add_source(Source(name="r", type=SourceType.LOCAL, path=str(repo)))
    store.add_component(
        Component(source="r", type=ComponentType.SKILL, name="my-skill", path="packages/my-skill")
    )
    store.save()
    res = sync(project)
    link = project / ".claude/skills/my-skill"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_text() == "# mine\n"
    assert not res.warnings


def test_explicit_path_missing_warns(project: Path, tmp_path: Path):
    repo = tmp_path / "empty"
    repo.mkdir()
    store = ConfigStore.load(project)
    store.add_source(Source(name="r", type=SourceType.LOCAL, path=str(repo)))
    store.add_component(Component(source="r", type=ComponentType.SKILL, name="ghost", path="nope"))
    store.save()
    res = sync(project)
    assert any("path 'nope' not found" in w for w in res.warnings)
    assert not (project / ".claude/skills/ghost").exists()


def test_unsupported_target_warns(project: Path, local_source: Path):
    # Claude is the only target; hooks are supported, but tools+skills on cursor would warn.
    # Add cursor target and a skill (unsupported on cursor) to trigger a warning.
    store = ConfigStore.load(project)
    store.doc["targets"].append("cursor")
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=ComponentType.SKILL, name="code-reviewer"))
    store.save()
    res = sync(project)
    assert any("does not support skill" in w for w in res.warnings)
