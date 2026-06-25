"""Tests for the configurable-mappings enhancement: source descriptors + target profiles."""

from __future__ import annotations

import json
from pathlib import Path

from agentry import discovery
from agentry.config import ConfigStore
from agentry.models import (
    Component,
    ComponentType,
    Config,
    ProfileRule,
    Source,
    SourceType,
    Strategy,
    Target,
)
from agentry.reconcile import sync
from agentry.targets import resolve_targets, unresolved_targets


# -- source descriptor ---------------------------------------------------


def _descriptor_source(root: Path) -> Path:
    """A source with a NON-standard layout described by agentry.yaml."""
    (root / "packages" / "code-reviewer").mkdir(parents=True)
    (root / "packages" / "code-reviewer" / "SKILL.md").write_text("# cr\n")
    (root / "ai" / "agents").mkdir(parents=True)
    (root / "ai" / "agents" / "planner.md").write_text("# planner\n")
    (root / "servers").mkdir()
    (root / "servers" / "github.json").write_text(json.dumps({"github": {"command": "npx"}}))
    (root / "agentry.yaml").write_text(
        "version: 1\n"
        "provides:\n"
        "  skill:\n"
        "    - { name: code-reviewer, path: packages/code-reviewer }\n"
        "  agent:\n"
        '    - { glob: "ai/agents/*.md" }\n'
        "  mcp:\n"
        '    - { glob: "servers/*.json" }\n'
    )
    return root


def test_descriptor_discovery(tmp_path: Path):
    src = _descriptor_source(tmp_path / "src")
    found = {(d.type, d.name) for d in discovery.discover(src)}
    assert (ComponentType.SKILL, "code-reviewer") in found
    assert (ComponentType.AGENT, "planner") in found
    assert (ComponentType.MCP, "github") in found
    assert len(found) == 3


def test_convention_fallback_without_descriptor(local_source: Path):
    # local_source (from conftest) has the standard layout and no descriptor.
    assert not (local_source / "agentry.yaml").exists()
    found = {(d.type, d.name) for d in discovery.discover(local_source)}
    assert (ComponentType.SKILL, "code-reviewer") in found
    assert len(found) == 6


def test_descriptor_explicit_path_honored_on_install(tmp_path: Path):
    src = _descriptor_source(tmp_path / "src")
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.SKILL, name="code-reviewer"))
    store.save()
    sync(proj)
    link = proj / ".claude/skills/code-reviewer"
    assert link.is_symlink()
    # Resolves to the descriptor's explicit (non-standard) path.
    assert (link / "SKILL.md").read_text() == "# cr\n"


# -- target profiles -----------------------------------------------------


def test_builtin_targets_resolved():
    specs = resolve_targets(Config())
    assert {"claude", "opencode", "cursor"} <= set(specs)


def test_profile_override_existing_target():
    cfg = Config(
        target_profiles={
            "claude": {ComponentType.TOOL: ProfileRule(strategy=Strategy.LINK, dest=".claude/plugins/tools/{name}")}
        }
    )
    specs = resolve_targets(cfg)
    assert specs["claude"].link_dest(ComponentType.TOOL, "x") == ".claude/plugins/tools/x"
    # other built-in mappings untouched
    assert specs["claude"].link_dest(ComponentType.SKILL, "x") == ".claude/skills/x"


def test_custom_tool_defined_in_config(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, ["mycli"]).save()
    store = ConfigStore.load(proj)
    # Inject a custom-tool profile directly into the raw doc.
    store.doc["target_profiles"] = {
        "mycli": {
            "skill": {"strategy": "link", "dest": ".mycli/skills/{name}"},
            "mcp": {"strategy": "merge", "file": ".mycli/cfg.json", "pointer": "mcpServers"},
        }
    }
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=ComponentType.SKILL, name="code-reviewer"))
    store.add_component(Component(source="s", type=ComponentType.MCP, name="github"))
    store.save()

    sync(proj)
    assert (proj / ".mycli/skills/code-reviewer").is_symlink()
    cfg = json.loads((proj / ".mycli/cfg.json").read_text())
    assert "github" in cfg["mcpServers"]


def test_unresolved_target_detected():
    cfg = Config(targets=["claude", "ghostide"])
    assert unresolved_targets(cfg) == ["ghostide"]


def test_unresolved_target_warns_on_sync(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.doc["targets"].append("ghostide")
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.save()
    res = sync(proj)
    assert any("ghostide" in w for w in res.warnings)
