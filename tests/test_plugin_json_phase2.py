"""T25 phase 2: env-warning scope, plugin-root hooks, `why`, and sync/doctor surfacing."""

from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import sync

_C = ComponentType


def _plugin_project(tmp_path: Path, manifest: dict, *comps: tuple[ComponentType, str]) -> Path:
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="p", type=SourceType.LOCAL, path=str(root)))
    for ctype, name in comps:
        store.add_component(Component(source="p", type=ctype, name=name))
    store.save()
    return proj


def test_other_plugin_json_fields_raise_no_env_warnings(tmp_path: Path):
    """`${VAR}` text in plugin.json fields agentry doesn't install (description, userConfig,
    …) must not trigger unset-variable warnings for the installed MCP entries (phase 2: only the selected entries are scanned)."""
    os.environ.pop("SOME_UNSET_T25_VAR", None)
    proj = _plugin_project(
        tmp_path,
        {
            "name": "demo",
            "description": "uses ${SOME_UNSET_T25_VAR} at runtime",
            "userConfig": {"token": {"description": "set ${SOME_UNSET_T25_VAR}"}},
            "mcpServers": {"a": {"command": "a"}},
        },
        (_C.MCP, "plugin-mcp"),
    )
    res = sync(proj)
    assert not any("SOME_UNSET_T25_VAR" in w for w in res.warnings)


def test_inline_hooks_with_plugin_root_are_skipped_without_link_merge_advice(tmp_path: Path):
    """An inline hook using ${CLAUDE_PLUGIN_ROOT} can't resolve outside an installed plugin
    and there is no script dir to link, so it is skipped — and the warning must not suggest a
    link+merge profile (that advice only fits file-based hook dirs)."""
    group = {"hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/run.sh"}]}
    proj = _plugin_project(
        tmp_path,
        {"name": "demo", "hooks": {"hooks": {"Stop": [group]}}},
        (_C.HOOK, "plugin-hooks"),
    )
    res = sync(proj)
    settings = proj / ".claude/settings.json"
    assert not settings.exists() or "Stop" not in json.loads(settings.read_text("utf-8")).get(
        "hooks", {}
    )
    (w,) = [w for w in res.warnings if w.startswith("p/hook/plugin-hooks")]
    assert "CLAUDE_PLUGIN_ROOT" in w
    assert "link+merge" not in w and "target_profiles" not in w


def test_why_shows_plugin_json_origin(tmp_path: Path, monkeypatch):
    proj = _plugin_project(
        tmp_path, {"name": "demo", "mcpServers": {"a": {"command": "a"}}}, (_C.MCP, "plugin-mcp")
    )
    sync(proj)
    monkeypatch.chdir(proj)
    result = CliRunner().invoke(app, ["why", "p/mcp/plugin-mcp"])
    assert result.exit_code == 0, result.output
    assert "defined in: .claude-plugin/plugin.json (mcpServers)" in " ".join(result.output.split())


def test_plugin_json_warnings_surface_in_sync_and_doctor(tmp_path: Path):
    """Phase 2 wires plugin.json warnings into sync and doctor, with PM's exact
    CLAUDE_PLUGIN_DATA text naming the component ref."""
    from agentry.doctor import run_checks

    proj = _plugin_project(
        tmp_path,
        {
            "name": "demo",
            "mcpServers": {"db": {"command": "db", "args": ["${CLAUDE_PLUGIN_DATA}/db"]}},
        },
        (_C.MCP, "plugin-mcp"),
    )
    text = (
        "p/mcp/plugin-mcp: references ${CLAUDE_PLUGIN_DATA}, which only exists inside an "
        "installed Claude plugin — installed as-is, but it won't resolve. Set CLAUDE_PLUGIN_DATA "
        "in your environment, or ask the plugin author for a fallback."
    )
    assert text in sync(proj).warnings
    assert any(text in c.message for c in run_checks(proj))


def test_unsupported_keys_from_two_sources_summarize_and_verbose_lists_each(
    tmp_path: Path, monkeypatch
):
    """Two plugin sources with unsupported keys fold into one summary line on sync; -v
    prints each source's own line."""
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    for name in ("p1", "p2"):
        root = tmp_path / name
        (root / ".claude-plugin").mkdir(parents=True)
        (root / "commands").mkdir()
        (root / "commands" / f"{name}.md").write_text("# c\n", "utf-8")
        manifest = {"name": name, "outputStyles": {"x": {}}}
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
        store.add_source(Source(name=name, type=SourceType.LOCAL, path=str(root)))
        store.add_component(Component(source=name, type=_C.COMMAND, name=name))
    store.save()

    monkeypatch.chdir(proj)
    runner = CliRunner()
    lines = [ln for ln in runner.invoke(app, ["sync"]).stderr.splitlines() if ln.startswith("  ! ")]
    summary = (
        "  ! 2 plugin sources use features agentry doesn't install — run with --verbose for the "
        "list"
    )
    assert lines.count(summary) == 1
    assert not any("no agentry equivalent" in ln for ln in lines)

    verbose = runner.invoke(app, ["sync", "-v"]).stderr.splitlines()
    for name in ("p1", "p2"):
        assert any(
            ln.startswith(f"  ! source '{name}': plugin.json ") and "no agentry equivalent" in ln
            for ln in verbose
        )
    assert summary not in verbose


def _root_skip(ref: str, event: str, cmd: str, target: str = "claude") -> str:
    return (
        f"{ref}: hook event '{event}' in .claude-plugin/plugin.json runs '{cmd}', and agentry "
        "doesn't support ${CLAUDE_PLUGIN_ROOT} paths in inline plugin hooks yet — skipped it on "
        f"{target}. Install the plugin in Claude Code itself to use this hook."
    )


def test_inline_root_hook_skips_only_that_event(tmp_path: Path):
    """Two inline events, only one using ${CLAUDE_PLUGIN_ROOT}: that event is skipped with
    the exact per-event warning; the other still installs."""
    root_cmd = "${CLAUDE_PLUGIN_ROOT}/run.sh"
    proj = _plugin_project(
        tmp_path,
        {
            "name": "demo",
            "hooks": {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": root_cmd}]}],
                    "SessionStart": [{"hooks": [{"type": "command", "command": "plain"}]}],
                }
            },
        },
        (_C.HOOK, "plugin-hooks"),
    )
    res = sync(proj)
    hooks = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]
    assert "Stop" not in hooks
    assert hooks["SessionStart"] == [{"hooks": [{"type": "command", "command": "plain"}]}]
    assert _root_skip("p/hook/plugin-hooks", "Stop", root_cmd) in res.warnings


def test_doctor_reports_inline_root_hook_skip(tmp_path: Path):
    from agentry.doctor import run_checks

    root_cmd = "${CLAUDE_PLUGIN_ROOT}/run.sh"
    proj = _plugin_project(
        tmp_path,
        {
            "name": "demo",
            "hooks": {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": root_cmd}]}]}},
        },
        (_C.HOOK, "plugin-hooks"),
    )
    sync(proj)
    plugin_checks = [c for c in run_checks(proj) if c.category == "plugin"]
    assert any(
        c.message == _root_skip("p/hook/plugin-hooks", "Stop", root_cmd) for c in plugin_checks
    )


def test_claude_plugin_data_set_in_env_suppresses_warning(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    proj = _plugin_project(
        tmp_path,
        {
            "name": "demo",
            "mcpServers": {"db": {"command": "db", "args": ["${CLAUDE_PLUGIN_DATA}/db"]}},
        },
        (_C.MCP, "plugin-mcp"),
    )
    res = sync(proj)
    assert not any("CLAUDE_PLUGIN_DATA" in w for w in res.warnings)


def test_claude_plugin_data_unset_gives_only_the_plugin_warning(tmp_path: Path, monkeypatch):
    """The plugin-specific warning replaces the generic unset-variable line (no double)."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    proj = _plugin_project(
        tmp_path,
        {
            "name": "demo",
            "mcpServers": {"db": {"command": "db", "args": ["${CLAUDE_PLUGIN_DATA}/db"]}},
        },
        (_C.MCP, "plugin-mcp"),
    )
    mentions = [w for w in sync(proj).warnings if "CLAUDE_PLUGIN_DATA" in w]
    assert len(mentions) == 1
    assert "is unset" not in mentions[0]


def test_why_defined_in_line_only_for_inline_components(tmp_path: Path, monkeypatch):
    proj = _plugin_project(
        tmp_path, {"name": "demo", "mcpServers": {"a": {"command": "a"}}}, (_C.MCP, "plugin-mcp")
    )
    sync(proj)
    monkeypatch.chdir(proj)
    out = " ".join(CliRunner().invoke(app, ["why", "p/mcp/plugin-mcp"]).output.split())
    assert "defined in: .claude-plugin/plugin.json (mcpServers)" in out
