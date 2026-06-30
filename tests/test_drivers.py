"""Tests for the per-agent driver layer (agentry.drivers)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.drivers import BUILTIN_DRIVERS, Driver, resolve_drivers
from agentry.drivers.claude import CLAUDE_HOOK_EVENTS
from agentry.models import (
    Component,
    ComponentType,
    Config,
    ProfileRule,
    Source,
    SourceType,
    Strategy,
)
from agentry.reconcile import sync

_C = ComponentType

ALL_AGENTS = {
    "claude",
    "opencode",
    "cursor",
    "codex",
    "gemini",
    "windsurf",
    "kimi",
    "copilot",
    "kiro",
    "agents",
}


def test_all_builtin_agents_present():
    drivers = resolve_drivers(Config())
    assert set(drivers) >= ALL_AGENTS
    assert set(BUILTIN_DRIVERS) == ALL_AGENTS


# -- claude policies ------------------------------------------------------


def test_claude_filters_unknown_hook_events():
    claude = BUILTIN_DRIVERS["claude"]
    kept, dropped = claude.filter_hook_events(
        {"SessionStart": [{"command": "ok"}], "Frobnicate": [{"command": "no"}]}
    )
    assert "SessionStart" in kept and "Frobnicate" not in kept
    assert dropped == ["Frobnicate"]
    assert "SessionStart" in CLAUDE_HOOK_EVENTS


def test_claude_namespaces_command_and_agent_only():
    claude = BUILTIN_DRIVERS["claude"]
    assert claude.namespaces(_C.COMMAND)
    assert claude.namespaces(_C.AGENT)
    assert not claude.namespaces(_C.SKILL)
    assert not claude.namespaces(_C.TOOL)


def test_non_claude_drivers_have_no_policies():
    for name in ALL_AGENTS - {"claude"}:
        driver = BUILTIN_DRIVERS[name]
        # No hook-event filtering: every key is kept untouched.
        kept, dropped = driver.filter_hook_events({"Whatever": [{"command": "x"}]})
        assert kept == {"Whatever": [{"command": "x"}]} and dropped == []
        # No namespacing.
        assert not driver.namespaces(_C.COMMAND)
        assert not driver.namespaces(_C.AGENT)


# -- composition with target_profiles ------------------------------------


def test_yaml_only_tool_gets_default_driver():
    cfg = Config(
        targets=["mycli"],
        target_profiles={
            "mycli": {_C.SKILL: ProfileRule(strategy=Strategy.LINK, dest=".mycli/skills/{name}")}
        },
    )
    drivers = resolve_drivers(cfg)
    mycli = drivers["mycli"]
    assert isinstance(mycli, Driver)
    assert mycli.link_dest(_C.SKILL, "x") == ".mycli/skills/x"
    # A purely-config tool carries no built-in policies.
    assert not mycli.namespaces(_C.COMMAND)
    assert mycli.filter_hook_events({"X": 1}) == ({"X": 1}, [])


def test_profile_override_preserves_claude_policies():
    # Overriding one claude type via target_profiles must keep claude's hook + namespace
    # policies AND apply the dest override (composition regression guard).
    cfg = Config(
        target_profiles={
            "claude": {
                _C.TOOL: ProfileRule(strategy=Strategy.LINK, dest=".claude/plugins/tools/{name}")
            }
        }
    )
    claude = resolve_drivers(cfg)["claude"]
    assert claude.link_dest(_C.TOOL, "x") == ".claude/plugins/tools/x"
    assert claude.link_dest(_C.SKILL, "x") == ".claude/skills/x"  # untouched
    assert claude.namespaces(_C.COMMAND)  # policy survived the override
    _, dropped = claude.filter_hook_events({"Nope": 1})
    assert dropped == ["Nope"]


# -- new-agent capability maps -------------------------------------------


@pytest.mark.parametrize(
    "name,ctype,expected",
    [
        ("gemini", _C.SKILL, ".gemini/skills/x"),
        ("gemini", _C.AGENT, ".gemini/agents/x.md"),
        ("gemini", _C.COMMAND, ".gemini/commands/x.toml"),
        ("windsurf", _C.SKILL, ".windsurf/skills/x"),
        ("windsurf", _C.COMMAND, ".windsurf/workflows/x.md"),
        ("kimi", _C.SKILL, ".kimi-code/skills/x"),
        ("codex", _C.SKILL, ".agents/skills/x"),
        ("copilot", _C.SKILL, ".github/skills/x"),
        ("copilot", _C.AGENT, ".github/agents/x.agent.md"),
        ("copilot", _C.COMMAND, ".github/prompts/x.prompt.md"),
        ("kiro", _C.SKILL, ".kiro/skills/x"),
        ("agents", _C.SKILL, ".agents/skills/x"),
    ],
)
def test_new_driver_link_dests(name, ctype, expected):
    assert BUILTIN_DRIVERS[name].link_dest(ctype, "x") == expected


def test_universal_agents_target_is_skills_only():
    # The tool-neutral .agents/ layout maps skills only; everything else is intentionally
    # unmapped (no cross-tool directory standard; AGENTS.md composition is a transform).
    agents = BUILTIN_DRIVERS["agents"]
    assert agents.supports(_C.SKILL)
    assert not agents.supports(_C.AGENT)
    assert not agents.supports(_C.COMMAND)
    assert not agents.supports(_C.MCP)
    assert not agents.supports(_C.HOOK)


@pytest.mark.parametrize(
    "name,pointer_file",
    [
        ("gemini", (".gemini/settings.json", "mcpServers")),
        ("kimi", (".kimi-code/mcp.json", "mcpServers")),
        ("copilot", (".vscode/mcp.json", "servers")),
        ("kiro", (".kiro/settings/mcp.json", "mcpServers")),
    ],
)
def test_new_driver_mcp_merge_dests(name, pointer_file):
    dest = BUILTIN_DRIVERS[name].merge_dest(_C.MCP)
    assert (dest.file, dest.pointer) == pointer_file


def test_copilot_mcp_accepts_stock_mcpservers_wrapper():
    # VS Code uses the top-level "servers" key; a stock .mcp.json fragment wraps entries
    # under "mcpServers" — the alias must let it install unchanged.
    dest = BUILTIN_DRIVERS["copilot"].merge_dest(_C.MCP)
    assert "mcpServers" in dest.wrapper_keys


def test_copilot_and_kiro_omit_unsupported_types():
    # Copilot: no project-level hook/tool file convention agentry can target.
    copilot = BUILTIN_DRIVERS["copilot"]
    assert copilot.supports(_C.SKILL) and copilot.supports(_C.MCP)
    assert not copilot.supports(_C.HOOK)
    assert not copilot.supports(_C.TOOL)
    # Kiro: agents are JSON (not translated); only skills + MCP are mapped.
    kiro = BUILTIN_DRIVERS["kiro"]
    assert kiro.supports(_C.SKILL) and kiro.supports(_C.MCP)
    assert not kiro.supports(_C.AGENT)
    assert not kiro.supports(_C.COMMAND)


def test_codex_and_windsurf_omit_unsupported_types():
    # Codex: skills + MCP (TOML merge); agent/command formats not translated.
    codex = BUILTIN_DRIVERS["codex"]
    assert codex.supports(_C.SKILL)
    assert codex.supports(_C.MCP)
    assert not codex.supports(_C.COMMAND)
    assert not codex.supports(_C.AGENT)
    # Codex MCP merges into config.toml under the snake_case [mcp_servers] table.
    assert codex.merge_dest(_C.MCP).file == ".codex/config.toml"
    assert codex.merge_dest(_C.MCP).pointer == "mcp_servers"
    # Windsurf: no custom agent definitions; project MCP undocumented.
    windsurf = BUILTIN_DRIVERS["windsurf"]
    assert not windsurf.supports(_C.AGENT)
    assert not windsurf.supports(_C.MCP)


# -- end-to-end through a new driver -------------------------------------


def test_sync_into_gemini_driver(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, ["gemini"]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=_C.SKILL, name="code-reviewer"))
    store.add_component(Component(source="s", type=_C.MCP, name="github"))
    store.save()

    res = sync(proj)
    assert (proj / ".gemini/skills/code-reviewer").is_symlink()
    settings = json.loads((proj / ".gemini/settings.json").read_text())
    assert "github" in settings["mcpServers"]

    # Idempotent: a second sync changes nothing.
    res2 = sync(proj)
    assert res2.created == [] and res2.updated == [] and res2.removed == []
    assert not res.warnings or all("github" not in w for w in res.warnings)
