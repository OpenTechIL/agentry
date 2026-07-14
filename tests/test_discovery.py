from __future__ import annotations

from pathlib import Path

from agentry import discovery
from agentry.models import ComponentType


def test_repo_self_hosts_use_agentry_skill():
    """This repo's own skills/use-agentry/ is discoverable by convention (no descriptor)."""
    repo_root = Path(__file__).resolve().parent.parent
    found = {(d.type, d.name) for d in discovery.discover(repo_root)}
    assert (ComponentType.SKILL, "use-agentry") in found
    skill_md = repo_root / "skills" / "use-agentry" / "SKILL.md"
    assert skill_md.is_file()


def test_discover_all_types(local_source: Path):
    found = {(d.type, d.name) for d in discovery.discover(local_source)}
    assert (ComponentType.SKILL, "code-reviewer") in found
    assert (ComponentType.AGENT, "planner") in found
    assert (ComponentType.COMMAND, "deploy") in found
    assert (ComponentType.TOOL, "fmt") in found
    assert (ComponentType.HOOK, "pre-commit-fmt") in found
    assert (ComponentType.MCP, "github") in found
    assert len(found) == 6


def test_merge_variants_carry_harness_affinity(tmp_path: Path):
    """`hooks-cursor.json` is tagged for the cursor harness; the canonical file is not."""
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text('{"hooks": {"SessionStart": []}}')
    (root / "hooks" / "hooks-cursor.json").write_text('{"hooks": {"sessionStart": []}}')
    (root / "hooks" / "hooks-codex.json").write_text('{"hooks": {"SessionStart": []}}')
    by_name = {d.name: d for d in discovery.discover(root) if d.type is ComponentType.HOOK}
    assert by_name["hooks"].harness is None
    assert by_name["hooks-cursor"].harness == "cursor"
    assert by_name["hooks-codex"].harness == "codex"


def test_harness_suffix_ignores_bare_and_unknown_names():
    """Only `<base>-<harness>` with a known slug counts; bare/unknown names are left alone."""
    assert discovery.harness_suffix("hooks-cursor") == "cursor"
    assert discovery.harness_suffix("codex") is None  # bare slug, not a variant
    assert discovery.harness_suffix("using-superpowers") is None  # unknown suffix
    assert discovery.harness_suffix("pre-commit-fmt") is None  # unknown suffix
    assert discovery.harness_suffix("hooks") is None


def test_artifact_path(local_source: Path):
    p = discovery.artifact_path(local_source, ComponentType.SKILL, "code-reviewer")
    assert p.is_dir()
    p = discovery.artifact_path(local_source, ComponentType.AGENT, "planner")
    assert p.is_file() and p.suffix == ".md"


def test_discovers_root_mcp_json(tmp_path: Path):
    """A plugin-style root `.mcp.json` is surfaced as a single `mcp` component."""
    root = tmp_path / "plugin"
    root.mkdir()
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"weather": {"type": "http", "url": "https://example.com/mcp"}}}'
    )
    found = {(d.type, d.name): d.path for d in discovery.discover(root)}
    assert (ComponentType.MCP, "mcp") in found
    assert found[(ComponentType.MCP, "mcp")] == root / ".mcp.json"


def test_mcp_json_unprefixed_also_found(tmp_path: Path):
    """`mcp.json` (no leading dot) at the root works too."""
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "mcp.json").write_text('{"mcpServers": {"x": {"type": "http", "url": "u"}}}')
    found = {(d.type, d.name) for d in discovery.discover(root)}
    assert (ComponentType.MCP, "mcp") in found


def test_root_mcp_does_not_duplicate_mcp_dir(tmp_path: Path):
    """An `mcp/mcp.json` already claiming the `mcp` name wins; the root file is skipped."""
    root = tmp_path / "plugin"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "mcp.json").write_text('{"a": {"command": "x"}}')
    (root / ".mcp.json").write_text('{"mcpServers": {"b": {"type": "http", "url": "u"}}}')
    mcp = [d for d in discovery.discover(root) if d.type is ComponentType.MCP and d.name == "mcp"]
    assert len(mcp) == 1
    assert mcp[0].path == root / "mcp" / "mcp.json"


def _apm_package(root: Path) -> Path:
    """A Microsoft apm package: primitives live under .apm/ with apm's dir names/extensions."""
    (root / ".apm" / "skills" / "style-checker").mkdir(parents=True)
    (root / ".apm" / "skills" / "style-checker" / "SKILL.md").write_text("# style\n")
    (root / ".apm" / "agents").mkdir(parents=True)
    (root / ".apm" / "agents" / "design-reviewer.agent.md").write_text("# reviewer\n")
    (root / ".apm" / "prompts").mkdir(parents=True)
    (root / ".apm" / "prompts" / "design-review.prompt.md").write_text("# prompt\n")
    (root / ".apm" / "instructions").mkdir(parents=True)
    (root / ".apm" / "instructions" / "design-standards.instructions.md").write_text("# std\n")
    return root


def test_discover_apm_package_maps_primitives(tmp_path: Path):
    found = {(d.type, d.name): d for d in discovery.discover(_apm_package(tmp_path / "pkg"))}
    # apm skills/agents/prompts → agentry skill/agent/command, with compound suffixes stripped.
    assert (ComponentType.SKILL, "style-checker") in found
    assert (ComponentType.AGENT, "design-reviewer") in found
    assert (ComponentType.COMMAND, "design-review") in found
    # apm instructions have no agentry component type → skipped.
    assert not any(d.name == "design-standards" for d in found.values())
    assert len(found) == 3
    # Artifacts keep their real .apm/ paths so reconcile symlinks the right file.
    assert found[(ComponentType.AGENT, "design-reviewer")].path.name == "design-reviewer.agent.md"


def test_apm_package_installs_under_agentry_naming(tmp_path: Path):
    from agentry.config import ConfigStore
    from agentry.models import Component, Source, SourceType, Target
    from agentry.reconcile import sync

    pkg = _apm_package(tmp_path / "pkg")
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="apmpkg", type=SourceType.LOCAL, path=str(pkg)))
    store.add_component(
        Component(source="apmpkg", type=ComponentType.AGENT, name="design-reviewer")
    )
    store.add_component(Component(source="apmpkg", type=ComponentType.SKILL, name="style-checker"))
    store.save()
    sync(proj)
    # The .agent.md file installs under agentry's convention name (.md), via a symlink.
    agent_link = proj / ".claude/agents/design-reviewer.md"
    assert agent_link.is_symlink() and agent_link.read_text() == "# reviewer\n"
    assert (proj / ".claude/skills/style-checker").is_symlink()
