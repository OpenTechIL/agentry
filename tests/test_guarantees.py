"""Structural guarantees — agentry's safety/reproducibility properties, locked in as a
CI-enforced contract.

These are the properties Microsoft apm keeps regressing on (lockfile churn, silent
overwrite of hand-edited config, last-write-wins dropping a server). agentry handles them
by construction; this suite asserts they *stay* true. Each test names the apm issue it
guards against so the intent is auditable.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentry.config import ConfigStore
from agentry.lockfile import lock_path
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import status, sync

_C = ComponentType


def _project(proj: Path, src: Path, *components: Component) -> ConfigStore:
    proj.mkdir(exist_ok=True)
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for c in components:
        store.add_component(c)
    store.save()
    return store


def test_lockfile_is_byte_stable_across_runs(tmp_path: Path, local_source: Path):
    """A second sync with unchanged inputs rewrites the lock byte-for-byte (no timestamp
    churn). Guards apm #1702 (lockfile rewritten every run via a `generated_at` field)."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.SKILL, name="code-reviewer"))
    sync(proj)
    first = lock_path(proj).read_bytes()
    sync(proj)
    assert lock_path(proj).read_bytes() == first


def test_sync_is_idempotent_noop(tmp_path: Path, local_source: Path):
    """The one hard rule: a second sync changes nothing. Guards drift/churn regressions."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.SKILL, name="code-reviewer"))
    sync(proj)
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []


def test_remove_is_fully_reversible(tmp_path: Path, local_source: Path):
    """Disabling a component removes exactly its managed artifacts — symlink and merged key
    — and prunes empty dirs, leaving the tree as if it were never installed. Guards apm's
    stale-artifact class (#1729/#1730/#1831)."""
    proj = tmp_path / "proj"
    store = _project(
        proj,
        local_source,
        Component(source="s", type=_C.SKILL, name="code-reviewer"),
        Component(source="s", type=_C.MCP, name="github"),
    )
    sync(proj)
    assert (proj / ".claude/skills/code-reviewer").is_symlink()
    assert "github" in json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]

    store.set_enabled("s/skill/code-reviewer", False)
    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(proj)

    assert not (proj / ".claude/skills/code-reviewer").exists()
    # The MCP key is gone and the now-empty section/file is cleaned up, not left as a shell.
    assert not (proj / ".mcp.json").exists() or "github" not in json.loads(
        (proj / ".mcp.json").read_text(encoding="utf-8")
    ).get("mcpServers", {})


def test_emit_triggers_never_clobbers_hand_authored_memory(tmp_path: Path):
    """Registering skill triggers writes ONLY its marker-delimited block: hand-authored prose
    above and below survives, a refresh is byte-stable, and a description change touches only
    the managed span. The markdown analog of the JSON never-clobber guarantee."""
    from agentry.emit import (
        TRIGGERS_BEGIN,
        EmitItem,
        compose_triggers_block,
        merge_managed_block,
    )

    def block(desc: str) -> str:
        return compose_triggers_block(
            [EmitItem(_C.SKILL, "greeter", f"---\nname: greeter\ndescription: {desc}\n---\nbody\n")]
        )

    existing = "# CLAUDE.md\n\nHand-written rules.\n"
    merged = merge_managed_block(existing, block("Use when greeting."))
    assert merged.startswith("# CLAUDE.md\n\nHand-written rules.")  # prose above preserved
    assert "Use when greeting." in merged

    # Refresh with identical inputs is byte-for-byte stable (no churn).
    assert merge_managed_block(merged, block("Use when greeting.")) == merged

    # A trigger change rewrites only the block; surrounding prose is untouched.
    updated = merge_managed_block(merged, block("Use when saying farewell."))
    assert "Use when saying farewell." in updated
    assert "Use when greeting." not in updated
    assert updated.startswith("# CLAUDE.md\n\nHand-written rules.")
    assert updated.count(TRIGGERS_BEGIN) == 1


def test_merge_never_clobbers_hand_authored_keys(tmp_path: Path, local_source: Path):
    """Merging an MCP server preserves a hand-authored entry in the same file, and removing
    agentry's entry leaves the hand-authored one intact. Guards apm #20 / #1764 (silent
    overwrite of hand-edited config)."""
    proj = tmp_path / "proj"
    store = _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    # A pre-existing, hand-authored MCP server the user wrote themselves.
    (proj / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"handmade": {"command": "mine"}}}), encoding="utf-8"
    )

    sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["handmade"] == {"command": "mine"}  # untouched
    assert "github" in servers  # ours added alongside

    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers == {"handmade": {"command": "mine"}}  # only ours removed


def test_distinct_mcp_servers_never_drop_each_other(tmp_path: Path):
    """Two MCP components with distinct server names both survive the merge — agentry merges
    each fragment's own declared name and never derives one by truncation. Guards apm #1693
    (scoped-name collision silently drops a server, last-write-wins)."""
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "acme.json").write_text(
        json.dumps({"acme-mcp": {"command": "a"}}), encoding="utf-8"
    )
    (src / "mcp" / "other.json").write_text(
        json.dumps({"other-mcp": {"command": "b"}}), encoding="utf-8"
    )
    proj = tmp_path / "proj"
    _project(
        proj,
        src,
        Component(source="s", type=_C.MCP, name="acme"),
        Component(source="s", type=_C.MCP, name="other"),
    )
    sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert {"acme-mcp", "other-mcp"} <= set(servers)


def test_status_and_sync_share_one_resolution_path(tmp_path: Path, local_source: Path):
    """`status` reports `ok` for exactly what `sync` installed — audit can't invent drift
    that install never produced. Guards apm #1923/#1924 (audit ≠ install false drift)."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.SKILL, name="code-reviewer"))
    sync(proj)
    rows, _ = status(proj)
    assert rows and all(r.state == "ok" for r in rows)


# --- Malformed target config (D6): warn and skip, never a traceback ---------------------


def _read_cfg(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_sync_skips_a_malformed_merge_target_and_installs_the_rest(
    tmp_path: Path, local_source: Path
):
    """A truncated .mcp.json makes sync skip that merge (left byte-identical) while other
    components still install. Guards QA D6."""
    proj = tmp_path / "proj"
    _project(
        proj,
        local_source,
        Component(source="s", type=_C.MCP, name="github"),
        Component(source="s", type=_C.SKILL, name="code-reviewer"),
    )
    broken = '{"mcpServers": {'
    (proj / ".mcp.json").write_text(broken, encoding="utf-8")

    sync(proj)  # must not raise

    assert (proj / ".mcp.json").read_text(encoding="utf-8") == broken
    assert (proj / ".claude/skills/code-reviewer").is_symlink()


def test_status_does_not_crash_on_a_malformed_merge_target(tmp_path: Path, local_source: Path):
    """`status` reports on a project with a malformed .mcp.json instead of raising."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    sync(proj)
    (proj / ".mcp.json").write_text('{"mcpServers": {', encoding="utf-8")

    rows, _ = status(proj)  # must not raise
    assert all(r.state != "ok" for r in rows if ".mcp.json" in r.where)


def test_jsonc_comments_in_merge_target_are_left_unchanged(tmp_path: Path, local_source: Path):
    """A JSONC-style file (comments, trailing comma) is unparseable as JSON: agentry must not
    rewrite it (losing the comments), and sync must not crash."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    jsonc = '{\n  // my servers\n  "mcpServers": {"mine": {"command": "x"},},\n}\n'
    (proj / ".mcp.json").write_text(jsonc, encoding="utf-8")

    sync(proj)
    assert (proj / ".mcp.json").read_text(encoding="utf-8") == jsonc


def test_broken_codex_toml_is_left_unchanged(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CODEX]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=_C.MCP, name="github"))
    store.save()
    cfg = proj / ".codex" / "config.toml"
    cfg.parent.mkdir()
    broken = '[mcp_servers.mine\ncommand = "x"\n'
    cfg.write_text(broken, encoding="utf-8")

    sync(proj)  # must not raise
    assert cfg.read_text(encoding="utf-8") == broken


def test_remove_while_config_broken_is_retried_after_the_user_fixes_it(
    tmp_path: Path, local_source: Path
):
    """Disabling a merge component while its config is unparseable leaves the file alone
    and keeps the record; once the user repairs the file, the next sync removes our key
    (and only ours)."""
    proj = tmp_path / "proj"
    store = _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}), encoding="utf-8")
    sync(proj)
    assert "github" in _read_cfg(cfg)["mcpServers"]

    repaired = cfg.read_text(encoding="utf-8")
    broken = repaired.rstrip()[:-1]  # drop the closing brace
    cfg.write_text(broken, encoding="utf-8")
    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(proj)  # must not raise
    assert cfg.read_text(encoding="utf-8") == broken

    cfg.write_text(repaired, encoding="utf-8")
    sync(proj)
    assert _read_cfg(cfg)["mcpServers"] == {"mine": {"command": "x"}}


def test_link_merge_with_broken_config_installs_nothing(tmp_path: Path):
    """link+merge is all-or-nothing: if the settings file can't be parsed, the script dir
    symlink is not created either (no half install)."""
    src = tmp_path / "src"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (src / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "${PLUGIN_ROOT}/run.sh"}]}]
                }
            }
        ),
        encoding="utf-8",
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".agentry.yml").write_text(
        f"""version: 1
targets: [claude]
sources:
  - name: s
    type: local
    path: {src}
components:
  - source: s
    type: hook
    name: hooks
    path: hooks
target_profiles:
  claude:
    hook:
      strategy: link+merge
      dest: ".claude/hooks/agentry/{{name}}"
      file: .claude/settings.json
      pointer: hooks
      rewrite_from: "${{PLUGIN_ROOT}}"
      rewrite_to: "${{CLAUDE_PROJECT_DIR}}/.claude/hooks/agentry/{{name}}"
""",
        encoding="utf-8",
    )
    settings = proj / ".claude" / "settings.json"
    settings.parent.mkdir()
    broken = '{"hooks": {'
    settings.write_text(broken, encoding="utf-8")

    sync(proj)  # must not raise
    assert settings.read_text(encoding="utf-8") == broken
    link = proj / ".claude/hooks/agentry/hooks"
    assert not link.exists() and not link.is_symlink()

    settings.write_text("{}", encoding="utf-8")  # control: the same config installs once fixed
    sync(proj)
    assert link.is_symlink()
    assert "Stop" in _read_cfg(settings)["hooks"]
