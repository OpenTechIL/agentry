"""T25: discovery follows a Claude plugin's `.claude-plugin/plugin.json`.

Only for sources with plugin.json and no `agentry.yaml`. Phase 1 surfaces plugin.json
warnings through `discovery.plugin_warnings(root)`; sync/doctor surfacing is phase 2. `commands`/`agents` paths *replace*
the default scan; `skills`/`hooks`/`mcpServers` *add* to it. Inline hooks/mcpServers become
the components `plugin-hooks` / `plugin-mcp` and install exactly plugin.json's entries. A
name-only plugin.json behaves exactly as today. Hermetic: local sources under tmp_path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentry import discovery
from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import sync

_C = ComponentType


# --- fixture helpers ----------------------------------------------------------------------


def _write(root: Path, rel: str, text: str = "x\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _skill(root: Path, rel_dir: str, name: str) -> None:
    _write(root, f"{rel_dir}/{name}/SKILL.md", f"---\nname: {name}\ndescription: d\n---\n")


def _plugin(root: Path, manifest: dict | str) -> None:
    text = manifest if isinstance(manifest, str) else json.dumps(manifest)
    _write(root, ".claude-plugin/plugin.json", text)


def _g(cmd: str) -> dict:
    return {"hooks": [{"type": "command", "command": cmd}]}


def _warnings(root: Path) -> list[str]:
    """Phase 1 surfaces plugin.json warnings only through this function."""
    return discovery.plugin_warnings(root)


def _rejected(raw: str, why: str, key: str = "commands", src: str = "plugin") -> str:
    return (
        f"source '{src}': plugin.json '{key}' path '{raw}' {why} — skipped it. Use a relative "
        "path inside the plugin, e.g. './commands'."
    )


def _keys(root: Path) -> set[tuple[ComponentType, str]]:
    return set(discovery.index(root))


def _names(root: Path, ctype: ComponentType) -> set[str]:
    return {n for t, n in discovery.index(root) if t is ctype}


def _project(tmp_path: Path, root: Path, *comps: tuple[ComponentType, str]) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="p", type=SourceType.LOCAL, path=str(root)))
    for ctype, name in comps:
        store.add_component(Component(source="p", type=ctype, name=name))
    store.save()
    return proj


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    """A plugin repo with today's convention layout and a name-only plugin.json."""
    root = tmp_path / "plugin"
    _write(root, "commands/default.md")
    _write(root, "agents/default.md")
    _skill(root, "skills", "base")
    _plugin(root, {"name": "demo"})
    return root


# --- replace vs add ---------------------------------------------------------------------


def test_commands_path_replaces_default_scan(plugin: Path):
    _write(plugin, "custom/new.md")
    _plugin(plugin, {"name": "demo", "commands": "./custom"})
    assert _names(plugin, _C.COMMAND) == {"new"}  # commands/default.md no longer scanned


def test_agents_path_replaces_default_scan(plugin: Path):
    _write(plugin, "my-agents/helper.md")
    _plugin(plugin, {"name": "demo", "agents": "./my-agents"})
    assert _names(plugin, _C.AGENT) == {"helper"}


def test_skills_path_adds_to_default_scan(plugin: Path):
    _skill(plugin, "extra", "more")
    _plugin(plugin, {"name": "demo", "skills": "./extra"})
    assert _names(plugin, _C.SKILL) == {"base", "more"}


def test_hooks_path_adds_to_default_hooks_json(plugin: Path):
    _write(plugin, "hooks/hooks.json", json.dumps({"hooks": {"Stop": [_g("default")]}}))
    _write(plugin, "more/extra-hooks.json", json.dumps({"hooks": {"Stop": [_g("extra")]}}))
    _plugin(plugin, {"name": "demo", "hooks": "./more/extra-hooks.json"})
    hooks = _names(plugin, _C.HOOK)
    assert "hooks" in hooks and len(hooks) == 2  # the default and the plugin.json path


def test_mcp_path_adds_to_default_mcp_json(plugin: Path):
    _write(plugin, ".mcp.json", json.dumps({"mcpServers": {"a": {"command": "a"}}}))
    _write(plugin, "config/servers.json", json.dumps({"mcpServers": {"b": {"command": "b"}}}))
    _plugin(plugin, {"name": "demo", "mcpServers": "./config/servers.json"})
    assert len(_names(plugin, _C.MCP)) == 2


# --- inline hooks / mcpServers ----------------------------------------------------------


def test_inline_mcp_servers_install_exactly_plugin_entries(plugin: Path, tmp_path: Path):
    servers = {"a": {"command": "a"}, "b": {"command": "b", "args": ["-x"]}}
    _plugin(plugin, {"name": "demo", "mcpServers": servers})
    assert (_C.MCP, "plugin-mcp") in _keys(plugin)
    proj = _project(tmp_path, plugin, (_C.MCP, "plugin-mcp"))
    sync(proj)
    assert json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"] == servers


def test_inline_hooks_install_exactly_plugin_entries(plugin: Path, tmp_path: Path):
    _plugin(plugin, {"name": "demo", "hooks": {"hooks": {"Stop": [_g("inline")]}}})
    assert (_C.HOOK, "plugin-hooks") in _keys(plugin)
    proj = _project(tmp_path, plugin, (_C.HOOK, "plugin-hooks"))
    sync(proj)
    hooks = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]
    assert hooks == {"Stop": [_g("inline")]}


def test_plugin_json_hooks_and_hooks_json_both_load_and_append(plugin: Path, tmp_path: Path):
    _write(plugin, "hooks/hooks.json", json.dumps({"hooks": {"Stop": [_g("file")]}}))
    _plugin(plugin, {"name": "demo", "hooks": {"hooks": {"Stop": [_g("inline")]}}})
    proj = _project(tmp_path, plugin, (_C.HOOK, "hooks"), (_C.HOOK, "plugin-hooks"))
    sync(proj)
    stop = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]["Stop"]
    assert _g("file") in stop and _g("inline") in stop and len(stop) == 2  # T21 append


def test_plugin_hooks_name_collides_with_real_file_file_wins(plugin: Path, tmp_path: Path):
    _write(plugin, "hooks/plugin-hooks.json", json.dumps({"hooks": {"Stop": [_g("file")]}}))
    _plugin(plugin, {"name": "demo", "hooks": {"hooks": {"Stop": [_g("inline")]}}})
    assert discovery.index(plugin)[(_C.HOOK, "plugin-hooks")].name == "plugin-hooks.json"
    proj = _project(tmp_path, plugin, (_C.HOOK, "plugin-hooks"))
    sync(proj)
    stop = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]["Stop"]
    assert stop == [_g("file")]
    assert (
        "source 'plugin': plugin.json would add hook 'plugin-hooks', but hooks/plugin-hooks.json "
        "already provides it — installed the file one; the plugin.json one is not installed. "
        "Rename the file to install both."
    ) in _warnings(plugin)


# --- precedence and unchanged behaviour -----------------------------------------------------


def test_descriptor_beats_plugin_json(plugin: Path):
    _write(plugin, "custom/new.md")
    _plugin(plugin, {"name": "demo", "commands": "./custom"})
    _write(
        plugin,
        "agentry.yaml",
        json.dumps(
            {
                "version": 1,
                "provides": {"command": [{"name": "only", "path": "commands/default.md"}]},
            }
        ),
    )
    assert _names(plugin, _C.COMMAND) == {"only"}  # the descriptor wins outright (guard)


def test_name_only_plugin_json_is_identical_to_today(plugin: Path, tmp_path: Path):
    """Guard: a name-only plugin.json must not change discovery."""
    without = tmp_path / "plain"
    _write(without, "commands/default.md")
    _write(without, "agents/default.md")
    _skill(without, "skills", "base")
    assert _keys(plugin) == _keys(without)


def test_malformed_plugin_json_falls_back_to_convention_with_warning(plugin: Path, tmp_path: Path):
    _write(plugin, "custom/new.md")
    _plugin(plugin, '{"name": "demo", "commands": "./custom",')  # truncated JSON
    assert _names(plugin, _C.COMMAND) == {"default"}  # convention, as if no plugin.json
    (w,) = _warnings(plugin)
    assert w.startswith("source 'plugin': .claude-plugin/plugin.json is not valid (")
    assert w.endswith(
        ") — ignored it and used the convention layout (skills/, commands/, agents/, hooks/, "
        ".mcp.json). Fix the file, or ask the plugin author to."
    )
    proj = _project(tmp_path, plugin, (_C.COMMAND, "default"))
    sync(proj)
    assert (proj / ".claude/commands/default.md").is_symlink()


# --- path forms and rejection -------------------------------------------------------------


def test_dot_slash_and_list_forms(plugin: Path):
    _write(plugin, "one.md")
    _write(plugin, "more/two.md")
    _plugin(plugin, {"name": "demo", "commands": ["./one.md", "./more"]})
    assert _names(plugin, _C.COMMAND) == {"one", "two"}


def test_skills_path_as_root_or_as_skill_dir(plugin: Path):
    _skill(plugin, "lib", "rooted")  # lib/ holds skill dirs
    _write(plugin, "solo/SKILL.md", "---\nname: solo\ndescription: d\n---\n")  # solo/ is one
    _plugin(plugin, {"name": "demo", "skills": ["./lib", "./solo"]})
    assert _names(plugin, _C.SKILL) == {"base", "rooted", "solo"}


@pytest.mark.parametrize(
    "bad, why",
    [
        ("../outside", "leaves the plugin (..)"),
        ("/abs/cmds", "is absolute"),
        ("cmds\\win", "uses backslashes"),
    ],
    ids=["dotdot", "abs", "backslash"],
)
def test_unsafe_plugin_paths_are_rejected(plugin: Path, tmp_path: Path, bad: str, why: str):
    _write(tmp_path, "outside/evil.md")
    _plugin(plugin, {"name": "demo", "commands": bad})
    assert "evil" not in _names(plugin, _C.COMMAND)
    assert _rejected(bad, why) in _warnings(plugin)


def test_symlink_escaping_source_root_is_rejected(plugin: Path, tmp_path: Path):
    _write(tmp_path, "outside/evil.md")
    os.symlink(tmp_path / "outside", plugin / "linked")
    _plugin(plugin, {"name": "demo", "commands": "./linked"})
    assert "evil" not in _names(plugin, _C.COMMAND)
    assert _rejected("./linked", "resolves outside the source (symlink)") in _warnings(plugin)


def test_symlink_to_sibling_plugin_inside_source_is_allowed(tmp_path: Path):
    """A marketplace-style source (the repo) with the plugin in a subdir: plugins/a/cmds links
    to plugins/shared/cmds, which is outside the plugin dir but inside the *source*, so it is
    allowed and installs."""
    repo = tmp_path / "repo"
    _write(repo, "plugins/shared/cmds/shared.md", "# shared\n")
    a = repo / "plugins" / "a"
    a.mkdir(parents=True)
    os.symlink(repo / "plugins" / "shared" / "cmds", a / "cmds")
    _plugin(a, {"name": "a", "commands": "./cmds"})

    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="p", type=SourceType.LOCAL, path=str(repo), subdir="plugins/a"))
    store.add_component(Component(source="p", type=_C.COMMAND, name="shared"))
    store.save()
    res = sync(proj)
    assert not any("not provided" in w for w in res.warnings)
    assert (proj / ".claude/commands/shared.md").read_text("utf-8") == "# shared\n"


# --- warnings and skips ---------------------------------------------------------------------


def test_claude_plugin_data_warning_text(plugin: Path):
    _plugin(
        plugin,
        {
            "name": "demo",
            "mcpServers": {"db": {"command": "db", "args": ["${CLAUDE_PLUGIN_DATA}/db"]}},
        },
    )
    assert (
        "plugin/mcp/plugin-mcp: references ${CLAUDE_PLUGIN_DATA}, which only exists inside an "
        "installed Claude plugin — installed as-is, but it won't resolve. Set CLAUDE_PLUGIN_DATA "
        "in your environment, or ask the plugin author for a fallback."
    ) in _warnings(plugin)


def test_content_form_command_is_skipped(plugin: Path, tmp_path: Path):
    """A plugin.json command given as inline content (not a path) has no file to link."""
    _plugin(plugin, {"name": "demo", "commands": {"hello": {"content": "Say hi"}}})
    assert "hello" not in _names(plugin, _C.COMMAND)
    assert (
        "source 'plugin': plugin.json commands 'hello' is inline content, which agentry can't "
        "install as a file — skipped it. Ship it as the file commands/hello.md instead."
    ) in _warnings(plugin)


# --- enablement visibility (PM) -------------------------------------------------------------


def test_newly_discovered_components_are_not_auto_enabled(plugin: Path, tmp_path: Path):
    proj = _project(tmp_path, plugin, (_C.SKILL, "base"))
    sync(proj)
    _skill(plugin, "extra", "more")
    _plugin(plugin, {"name": "demo", "skills": "./extra"})
    sync(proj)
    names = [c.name for c in ConfigStore.load(proj).parsed().components]
    assert names == ["base"]  # not auto-added
    assert not (proj / ".claude/skills/more").exists()
    assert (_C.SKILL, "more") in _keys(plugin)  # but it is discoverable (agentry list)


def test_enabled_component_no_longer_provided_warns_and_is_removed(plugin: Path, tmp_path: Path):
    proj = _project(tmp_path, plugin, (_C.COMMAND, "default"))
    sync(proj)
    assert (proj / ".claude/commands/default.md").is_symlink()
    _write(plugin, "custom/new.md")
    _plugin(plugin, {"name": "demo", "commands": "./custom"})  # replaces commands/
    res = sync(proj)
    assert any("p/command/default" in w and "not provided" in w for w in res.warnings)
    assert not (proj / ".claude/commands/default.md").exists()


def test_missing_plugin_path_is_skipped_with_warning(plugin: Path):
    _plugin(plugin, {"name": "demo", "commands": "./nowhere"})
    assert _names(plugin, _C.COMMAND) == set()  # the path replaced the scan, but is absent
    assert (
        "source 'plugin': plugin.json 'commands' path './nowhere' does not exist — skipped it. "
        "Fix the path in plugin.json."
    ) in _warnings(plugin)


def test_mcp_bundle_is_skipped_with_warning(plugin: Path):
    _write(plugin, "server.mcpb", "binary")
    _plugin(plugin, {"name": "demo", "mcpServers": "./server.mcpb"})
    assert (_C.MCP, "plugin-mcp") not in _keys(plugin)
    assert (
        "source 'plugin': plugin.json mcpServers './server.mcpb' is an MCP bundle, which agentry "
        "can't install — skipped it. Ask the plugin author for a JSON mcpServers entry."
    ) in _warnings(plugin)


@pytest.mark.parametrize(
    "keys, listed",
    [
        (["outputStyles", "lspServers"], "`outputStyles` and `lspServers`"),
        (
            ["outputStyles", "lspServers", "userConfig"],
            "`outputStyles`, `lspServers` and `userConfig`",
        ),
    ],
    ids=["two", "three"],
)
def test_unsupported_plugin_key_is_reported(plugin: Path, keys: list[str], listed: str):
    """PM: every unsupported plugin.json key of a source is reported on ONE line."""
    _plugin(plugin, {"name": "demo", **{k: {"x": {}} for k in keys}})
    lines = [w for w in _warnings(plugin) if "no agentry equivalent" in w]
    assert lines == [
        f"source 'plugin': plugin.json {listed} have no agentry equivalent — skipped. They still "
        "work when the plugin is installed in Claude Code itself."
    ]


def test_single_unsupported_key_uses_singular(plugin: Path):
    _plugin(plugin, {"name": "demo", "outputStyles": {"x": {}}})
    lines = [w for w in _warnings(plugin) if "no agentry equivalent" in w]
    assert lines == [
        "source 'plugin': plugin.json `outputStyles` has no agentry equivalent — skipped. It still "
        "works when the plugin is installed in Claude Code itself."
    ]


@pytest.mark.parametrize(
    "key, example",
    [
        ("agents", "./agents"),
        ("skills", "./skills"),
        ("hooks", "./hooks/hooks.json"),
        ("mcpServers", "./.mcp.json"),
    ],
)
def test_rejected_path_hint_uses_the_keys_own_default(plugin: Path, key: str, example: str):
    _plugin(plugin, {"name": "demo", key: "/abs/thing"})
    assert (
        f"source 'plugin': plugin.json '{key}' path '/abs/thing' is absolute — skipped it. Use a "
        f"relative path inside the plugin, e.g. '{example}'."
    ) in _warnings(plugin)


# --- manifest unwrap contract (architect) -------------------------------------------------

_TOP_LEVEL = {"name", "description", "version", "mcpServers", "hooks"}


def test_plugin_inline_list_mcp_merges_only_servers(plugin: Path, tmp_path: Path):
    """A list-form mcpServers ([{...servers...}]) merges only the servers — never the
    manifest's own top-level fields."""
    _plugin(
        plugin,
        {"name": "demo", "description": "d", "mcpServers": [{"a": {"command": "a"}}]},
    )
    proj = _project(tmp_path, plugin, (_C.MCP, "plugin-mcp"))
    sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"]
    assert servers == {"a": {"command": "a"}}
    assert not (_TOP_LEVEL & set(servers))


@pytest.mark.parametrize("field", ["mcpServers", "hooks"])
@pytest.mark.parametrize(
    "bad, raw",
    [(5, "5"), ([1], "1"), (["./x.json", 2], "2")],
    ids=["number", "list-of-int", "mixed"],
)
def test_plugin_manifest_never_merges_top_level_fields(
    plugin: Path, tmp_path: Path, field: str, bad, raw: str
):
    """For any bad shape, nothing from plugin.json's top level is merged, and the non-path,
    non-object value is reported exactly."""
    _plugin(plugin, {"name": "demo", "description": "d", field: bad})
    ctype, comp = (_C.MCP, "plugin-mcp") if field == "mcpServers" else (_C.HOOK, "plugin-hooks")
    proj = _project(tmp_path, plugin, (ctype, comp))
    sync(proj)
    for path, pointer in ((".mcp.json", "mcpServers"), (".claude/settings.json", "hooks")):
        f = proj / path
        if f.is_file():
            assert not (_TOP_LEVEL & set(json.loads(f.read_text("utf-8")).get(pointer, {})))
    example = "./.mcp.json" if field == "mcpServers" else "./hooks/hooks.json"
    assert (
        f"source 'plugin': plugin.json '{field}' value {raw} is neither a path nor an object — "
        f"skipped it. Use a relative path inside the plugin (e.g. '{example}') or an inline object."
    ) in _warnings(plugin)


def test_ordinary_fragment_keeps_one_level_unwrap(tmp_path: Path):
    """Guard: the second unwrap is for plugin.json manifests only. An ordinary mcp/*.json or
    hooks/*.json shaped {"mcpServers": {"mcpServers": …}} / {"hooks": {"hooks": …}} keeps
    today's single unwrap."""
    src = tmp_path / "src"
    _write(src, "mcp/x.json", json.dumps({"mcpServers": {"mcpServers": {"a": {"command": "a"}}}}))
    _write(src, "hooks/h.json", json.dumps({"hooks": {"hooks": {"Stop": [_g("c")]}}}))
    proj = _project(tmp_path, src, (_C.MCP, "x"), (_C.HOOK, "h"))
    res = sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"]
    assert servers == {"mcpServers": {"a": {"command": "a"}}}  # one level, as before
    settings = proj / ".claude/settings.json"
    assert not settings.exists() or "Stop" not in json.loads(settings.read_text("utf-8")).get(
        "hooks", {}
    )
    assert "p/hook/h: hook event 'hooks' is not a recognized claude event — skipped" in res.warnings


def test_list_form_mcp_merges_objects_in_order_first_duplicate_wins(plugin: Path, tmp_path: Path):
    """Object items merge in order, path strings are ignored, and a server listed twice keeps
    the first definition with a note."""
    _plugin(
        plugin,
        {
            "name": "demo",
            "mcpServers": [{"a": {"command": "1"}}, {"a": {"command": "2"}, "b": {"command": "b"}}],
        },
    )
    proj = _project(tmp_path, plugin, (_C.MCP, "plugin-mcp"))
    res = sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"]
    assert servers == {"a": {"command": "1"}, "b": {"command": "b"}}
    assert (
        "p/mcp/plugin-mcp: plugin.json 'mcpServers' lists 'a' more than once — merged the first "
        "one and skipped the rest. Remove the duplicate from plugin.json."
    ) in res.warnings


def test_list_form_hooks_concatenate_groups_in_item_order(plugin: Path, tmp_path: Path):
    _plugin(plugin, {"name": "demo", "hooks": [{"Stop": [_g("one")]}, {"Stop": [_g("two")]}]})
    proj = _project(tmp_path, plugin, (_C.HOOK, "plugin-hooks"))
    sync(proj)
    stop = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]["Stop"]
    assert stop == [_g("one"), _g("two")]


@pytest.mark.parametrize(
    "value, what",
    [(5, "is a number"), ("str", "is a string"), (True, "is true/false"), (None, "is missing")],
    ids=["number", "string", "bool", "null"],
)
def test_manifest_entries_non_object_merges_nothing(tmp_path: Path, value, what):
    """The defensive path of manifest_entries (discovery normally rejects these first)."""
    from agentry.plugin_manifest import manifest_entries

    _plugin(tmp_path, {"name": "demo", "description": "d", "mcpServers": value})
    entries, notes = manifest_entries(tmp_path / ".claude-plugin" / "plugin.json", _C.MCP)
    assert entries == {}
    assert notes == [
        f"plugin.json 'mcpServers' {what}, not an object — merged nothing. Make it an object or a "
        "list of objects in plugin.json, or ask the plugin author to."
        if what != "is missing"
        else "plugin.json 'mcpServers' is missing — merged nothing. Make it an object or a list "
        "of objects in plugin.json, or ask the plugin author to."
    ]


def test_manifest_entries_absent_key_merges_nothing(tmp_path: Path):
    from agentry.plugin_manifest import manifest_entries

    _plugin(tmp_path, {"name": "demo"})
    entries, notes = manifest_entries(tmp_path / ".claude-plugin" / "plugin.json", _C.HOOK)
    assert entries == {}
    assert notes == [
        "plugin.json 'hooks' is missing — merged nothing. Make it an object or a list of objects "
        "in plugin.json, or ask the plugin author to."
    ]
