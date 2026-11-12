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

import pytest

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


# --- Merge ownership: hand-added entries survive install, re-sync and uninstall ----------
# Regression tests for QA D1-D3 / field D1-D2 / arch#2 (task T3). They assert the user-visible
# invariant only (user values preserved), never internal manifest structure, so they hold
# whatever ownership model ADR-001 settles on. Warning-text assertions wait for ADR-001.


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _hook_commands(proj: Path, event: str) -> list[str]:
    """Every hook command registered for ``event`` in .claude/settings.json."""
    groups = _read_json(proj / ".claude/settings.json").get("hooks", {}).get(event, [])
    return [h.get("command") for g in groups for h in g.get("hooks", [])]


def _native_hook(src: Path, name: str, event: str, command: str) -> None:
    """A Claude-native hook fragment: an event-keyed array of matcher groups."""
    (src / "hooks").mkdir(parents=True, exist_ok=True)
    (src / "hooks" / f"{name}.json").write_text(
        json.dumps(
            {
                "hooks": {
                    event: [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]
                }
            }
        ),
        encoding="utf-8",
    )


def test_merge_never_clobbers_same_named_hand_authored_key(tmp_path: Path, local_source: Path):
    """A hand-authored MCP server that shares a name with an agentry component is left
    byte-for-byte as the user wrote it through sync, re-sync and disable. Guards QA D1."""
    proj = tmp_path / "proj"
    store = _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    mine = {"command": "MINE", "args": ["--mine"]}
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"github": mine}}), encoding="utf-8")

    sync(proj)
    assert _read_json(proj / ".mcp.json")["mcpServers"]["github"] == mine
    res = sync(proj)
    assert _read_json(proj / ".mcp.json")["mcpServers"]["github"] == mine
    assert res.created == [] and res.updated == [] and res.removed == []  # re-sync is a no-op

    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(proj)
    assert _read_json(proj / ".mcp.json").get("mcpServers", {}).get("github") == mine


def test_hook_pack_preserves_user_entries_on_same_event(tmp_path: Path):
    """A hook pack shipping PreToolUse adds its entry alongside the user's own PreToolUse
    hooks; uninstalling removes only agentry's entry. Guards QA D2."""
    src = tmp_path / "src"
    _native_hook(src, "guard", "PreToolUse", "agentry-guard")
    proj = tmp_path / "proj"
    store = _project(proj, src, Component(source="s", type=_C.HOOK, name="guard"))
    (proj / ".claude").mkdir(parents=True, exist_ok=True)
    (proj / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "USER-HOOK"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    sync(proj)
    cmds = _hook_commands(proj, "PreToolUse")
    assert "USER-HOOK" in cmds and "agentry-guard" in cmds
    sync(proj)
    assert sorted(_hook_commands(proj, "PreToolUse")) == sorted(cmds)  # re-sync: no dupes

    store.set_enabled("s/hook/guard", False)
    store.save()
    sync(proj)
    assert _hook_commands(proj, "PreToolUse") == ["USER-HOOK"]


def test_two_hook_components_on_same_event_coexist(tmp_path: Path):
    """Two components both hooking PostToolUse each install; disabling one leaves the
    other in place. Guards field D2 (last-write-wins on a shared event key)."""
    src = tmp_path / "src"
    _native_hook(src, "fmt", "PostToolUse", "run-fmt")
    _native_hook(src, "lint", "PostToolUse", "run-lint")
    proj = tmp_path / "proj"
    store = _project(
        proj,
        src,
        Component(source="s", type=_C.HOOK, name="fmt"),
        Component(source="s", type=_C.HOOK, name="lint"),
    )

    sync(proj)
    assert {"run-fmt", "run-lint"} <= set(_hook_commands(proj, "PostToolUse"))

    store.set_enabled("s/hook/fmt", False)
    store.save()
    sync(proj)
    assert _hook_commands(proj, "PostToolUse") == ["run-lint"]


def test_merge_never_replaces_non_object_section(tmp_path: Path, local_source: Path):
    """A target section the user set to a non-object value is never silently replaced.
    Guards QA D3."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": ["user", "list"]}), encoding="utf-8")

    sync(proj)
    assert _read_json(proj / ".mcp.json")["mcpServers"] == ["user", "list"]


def test_hand_edited_managed_merge_entry_reports_drift(tmp_path: Path, local_source: Path):
    """Hand-editing the value of an agentry-managed merge entry is reported as drift by
    `status`, not as `ok`. Guards field D3 (presence-only drift check)."""
    proj = tmp_path / "proj"
    _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    sync(proj)
    doc = _read_json(proj / ".mcp.json")
    doc["mcpServers"]["github"]["args"] = ["--edited-by-hand"]
    (proj / ".mcp.json").write_text(json.dumps(doc), encoding="utf-8")

    rows, _ = status(proj)
    mcp_rows = [r for r in rows if ".mcp.json" in r.where]
    assert mcp_rows, "status must report the merged MCP entry"
    assert all(r.state != "ok" for r in mcp_rows)


# --- ADR-001 PR1: named-mode ownership, proven by content fingerprint ---------------------


_GH = {"command": "npx", "args": ["-y", "server-github"]}  # what local_source ships


def _mcp_project(tmp_path: Path, local_source: Path) -> tuple[Path, ConfigStore]:
    proj = tmp_path / "proj"
    store = _project(proj, local_source, Component(source="s", type=_C.MCP, name="github"))
    return proj, store


def _disable(store: ConfigStore, ref: str) -> None:
    store.set_enabled(ref, False)
    store.save()


def _mcp_rows(proj: Path):
    rows, _ = status(proj)
    return [r for r in rows if ".mcp.json" in r.where]


def test_merge_refuses_preexisting_same_name_key(tmp_path: Path, local_source: Path):
    """W1: a differing user entry under our key is refused, and the file is never written."""
    proj, store = _mcp_project(tmp_path, local_source)
    cfg = proj / ".mcp.json"
    original = json.dumps({"mcpServers": {"github": {"command": "MINE"}}})
    cfg.write_text(original, encoding="utf-8")

    res = sync(proj)
    assert any(
        "mcpServers.github" in w and "not added by agentry" in w and "left unchanged" in w
        for w in res.warnings
    )
    assert cfg.read_text(encoding="utf-8") == original
    res2 = sync(proj)
    assert res2.created == [] and res2.updated == [] and res2.removed == []
    assert cfg.read_text(encoding="utf-8") == original
    _disable(store, "s/mcp/github")
    sync(proj)
    assert cfg.read_text(encoding="utf-8") == original


def test_identical_preexisting_entry_never_removed(tmp_path: Path, local_source: Path):
    """W1a: an identical user entry satisfies the component, is marked preexisting, and is
    never removed by agentry."""
    from agentry.manifest import load_manifest

    proj, store = _mcp_project(tmp_path, local_source)
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"github": _GH}}), encoding="utf-8")

    first = sync(proj)
    assert any("identical" in w and "using yours" in w for w in first.warnings)
    (rec,) = [m for m in load_manifest(proj).merges if m.file == ".mcp.json"]
    assert rec.entries["github"].preexisting is True
    assert [r.state for r in _mcp_rows(proj)] == ["ok"]
    second = sync(proj)
    assert not any("identical" in w for w in second.warnings)  # first sync only

    _disable(store, "s/mcp/github")
    sync(proj)
    assert _read_json(cfg)["mcpServers"]["github"] == _GH


def test_owned_value_edited_is_kept_and_reported_drift(tmp_path: Path, local_source: Path):
    """W2/W4: a hand-edit of an entry agentry wrote is kept on sync, shown as drift, and
    left in place on removal."""
    proj, store = _mcp_project(tmp_path, local_source)
    sync(proj)
    cfg = proj / ".mcp.json"
    doc = _read_json(cfg)
    edited = {"command": "npx", "args": ["--edited"]}
    doc["mcpServers"]["github"] = edited
    cfg.write_text(json.dumps(doc), encoding="utf-8")

    res = sync(proj)
    assert any("edited after agentry installed it" in w for w in res.warnings)
    assert _read_json(cfg)["mcpServers"]["github"] == edited
    (row,) = _mcp_rows(proj)
    assert row.state == "drift" and row.note and "edited by hand" in row.note

    _disable(store, "s/mcp/github")
    res = sync(proj)
    assert any("left in place on removal" in w for w in res.warnings)
    assert _read_json(cfg)["mcpServers"]["github"] == edited


def test_upstream_change_of_owned_entry_is_an_update(tmp_path: Path, local_source: Path):
    proj, _ = _mcp_project(tmp_path, local_source)
    sync(proj)
    new = {"command": "npx", "args": ["-y", "server-github@2"]}
    (local_source / "mcp" / "github.json").write_text(json.dumps({"github": new}), "utf-8")

    res = sync(proj)
    assert _read_json(proj / ".mcp.json")["mcpServers"]["github"] == new
    assert any(".mcp.json" in u for u in res.updated)
    assert not any(".mcp.json" in c for c in res.created)


def test_owned_entry_deleted_by_user_is_recreated(tmp_path: Path, local_source: Path):
    proj, _ = _mcp_project(tmp_path, local_source)
    sync(proj)
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}), encoding="utf-8")

    res = sync(proj)
    assert _read_json(cfg)["mcpServers"] == {"mine": {"command": "x"}, "github": _GH}
    assert any(".mcp.json" in c for c in res.created)


def test_non_object_section_skipped_with_warning(tmp_path: Path, local_source: Path):
    proj, _ = _mcp_project(tmp_path, local_source)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": ["x"]}), encoding="utf-8")

    res = sync(proj)
    assert any("is a list, not an object" in w and "not merged" in w for w in res.warnings)
    (row,) = _mcp_rows(proj)
    assert row.state == "missing" and row.note


def test_v1_manifest_loads_and_migrates(tmp_path: Path, local_source: Path):
    """A manifest written by an older agentry (no per-key `entries`) is migrated: status
    does so in memory only, sync persists v2 with a content fingerprint, and removal still
    takes out only our key."""
    from agentry.installers.merge import fingerprint
    from agentry.manifest import manifest_path

    proj, store = _mcp_project(tmp_path, local_source)
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}), encoding="utf-8")
    sync(proj)
    mp = manifest_path(proj)
    payload = json.loads(mp.read_text(encoding="utf-8"))
    payload["version"] = 1
    for rec in payload.get("merges", []):
        rec.pop("entries", None)
    mp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    v1_bytes = mp.read_bytes()

    status(proj)
    assert mp.read_bytes() == v1_bytes  # status never writes the manifest

    sync(proj)
    saved = json.loads(mp.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    (rec,) = [m for m in saved["merges"] if m["file"] == ".mcp.json"]
    assert rec["entries"]["github"]["fingerprint"] == fingerprint(_GH)

    _disable(store, "s/mcp/github")
    sync(proj)
    assert _read_json(cfg)["mcpServers"] == {"mine": {"command": "x"}}


def test_doctor_strict_exits_1_on_w1_refusal(
    tmp_path: Path, local_source: Path, monkeypatch: pytest.MonkeyPatch
):
    from typer.testing import CliRunner

    from agentry.cli import app

    proj, _ = _mcp_project(tmp_path, local_source)
    (proj / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "MINE"}}}), encoding="utf-8"
    )
    sync(proj)
    monkeypatch.chdir(proj)
    assert CliRunner().invoke(app, ["doctor", "--strict"]).exit_code == 1


def test_doctor_hint_uses_note_not_run_sync(tmp_path: Path, local_source: Path):
    """A refused merge can't be fixed by re-running sync, so doctor must not suggest it."""
    from agentry.doctor import run_checks

    proj, _ = _mcp_project(tmp_path, local_source)
    (proj / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "MINE"}}}), encoding="utf-8"
    )
    sync(proj)
    drift = [c.message for c in run_checks(proj) if c.category == "drift"]
    (msg,) = [m for m in drift if "s/mcp/github" in m]
    assert "not added by agentry" in msg
    assert "run `agentry sync`)" not in msg


def test_duplicate_claim_has_exactly_one_owner(tmp_path: Path):
    """Two components shipping the same MCP key: one owns it, the other is skipped with a
    warning, and disabling the skipped one leaves the key. (Interim W5; PR3 makes the
    winner config-order deterministic, so this asserts only 'exactly one owner'.)"""
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    vals = {"a": {"command": "from-a"}, "b": {"command": "from-b"}}
    for name, val in vals.items():
        (src / "mcp" / f"{name}.json").write_text(json.dumps({"dup": val}), encoding="utf-8")
    proj = tmp_path / "proj"
    store = _project(
        proj,
        src,
        Component(source="s", type=_C.MCP, name="a"),
        Component(source="s", type=_C.MCP, name="b"),
    )

    res = sync(proj)
    dup_warnings = [w for w in res.warnings if "is already provided by" in w]
    assert len(dup_warnings) == 1
    loser = "a" if dup_warnings[0].startswith("s/mcp/a") else "b"
    winner = "b" if loser == "a" else "a"
    assert _read_json(proj / ".mcp.json")["mcpServers"]["dup"] == vals[winner]

    _disable(store, f"s/mcp/{loser}")
    sync(proj)
    assert _read_json(proj / ".mcp.json")["mcpServers"]["dup"] == vals[winner]


def test_link_merge_refused_merge_creates_no_symlink(tmp_path: Path):
    """link+merge is atomic: when the merge half is refused (W1, here a list-vs-object shape
    conflict), the link half is not installed either."""
    src = tmp_path / "src"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (src / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "r"}]}]}}),
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
""",
        encoding="utf-8",
    )
    settings = proj / ".claude" / "settings.json"
    settings.parent.mkdir()
    # Rev 5: a user *list* on the same event no longer refuses (ours is appended), so the
    # refusal under test is a shape conflict: the user's Stop is an object, not a list.
    mine = json.dumps({"hooks": {"Stop": {"x": 1}}})
    settings.write_text(mine, encoding="utf-8")

    sync(proj)
    assert settings.read_text(encoding="utf-8") == mine
    link = proj / ".claude/hooks/agentry/hooks"
    assert not link.exists() and not link.is_symlink()


def test_link_merge_user_list_on_same_event_merges_alongside(tmp_path: Path):
    """Rev 5: a user's own list on the same event no longer refuses a link+merge hook. Ours
    is appended and linked; the user's group stays byte-identical, also through disable."""
    src = tmp_path / "src"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (src / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "r"}]}]}}),
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
""",
        encoding="utf-8",
    )
    settings = proj / ".claude" / "settings.json"
    settings.parent.mkdir()
    mine = {"hooks": [{"type": "command", "command": "MINE"}]}
    settings.write_text(json.dumps({"hooks": {"Stop": [mine]}}), encoding="utf-8")

    sync(proj)
    stop = _read_json(settings)["hooks"]["Stop"]
    assert stop[0] == mine and len(stop) == 2  # user's first and unchanged, ours appended
    assert (proj / ".claude/hooks/agentry/hooks").is_symlink()

    store = ConfigStore.load(proj)
    store.set_enabled("s/hook/hooks", False)
    store.save()
    sync(proj)
    assert _read_json(settings)["hooks"]["Stop"] == [mine]


def test_fingerprint_is_canonical():
    import tomlkit

    from agentry.installers.merge import fingerprint

    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})
    assert fingerprint({"c": "café"}) == fingerprint({"c": "café"})
    doc = tomlkit.parse('[srv]\ncommand = "npx"\nargs = ["-y", "café"]\n')
    assert fingerprint(doc["srv"]) == fingerprint({"command": "npx", "args": ["-y", "café"]})


def _downgrade_manifest_to_v1(proj: Path) -> None:
    """Rewrite the manifest as an older agentry wrote it: version 1, no per-key entries."""
    from agentry.manifest import manifest_path

    mp = manifest_path(proj)
    payload = json.loads(mp.read_text(encoding="utf-8"))
    payload["version"] = 1
    for section in ("merges", "link_merges"):
        for rec in payload.get(section, []):
            rec.pop("entries", None)
    mp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


_USER_GROUP = {"matcher": "Bash", "hooks": [{"type": "command", "command": "USER-HOOK"}]}


def _v1_hook_with_user_addition(tmp_path: Path):
    """guard installed PreToolUse [agentry-guard]; the user appended _USER_GROUP; the
    manifest is then downgraded to v1 (no per-key proof)."""
    src = tmp_path / "src"
    _native_hook(src, "guard", "PreToolUse", "agentry-guard")
    proj = tmp_path / "proj"
    store = _project(proj, src, Component(source="s", type=_C.HOOK, name="guard"))
    sync(proj)
    _downgrade_manifest_to_v1(proj)
    settings = proj / ".claude/settings.json"
    doc = _read_json(settings)
    doc["hooks"]["PreToolUse"].append(_USER_GROUP)
    settings.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return proj, store, settings


def test_v1_hook_key_with_user_additions_not_owned(tmp_path: Path):
    """A v1 record claims a hook-event key, but the user has since appended their own group.
    The user's group is never adopted: it survives sync and disable byte-identical.
    (Rev 5 §8: status is ok because agentry's identical element is claimed.)"""
    proj, store, settings = _v1_hook_with_user_addition(tmp_path)

    rows, _ = status(proj)
    (row,) = [r for r in rows if r.ref == "s/hook/guard"]
    assert row.state == "ok"
    assert _USER_GROUP in _read_json(settings)["hooks"]["PreToolUse"]

    _disable(store, "s/hook/guard")
    sync(proj)
    assert _USER_GROUP in _read_json(settings)["hooks"]["PreToolUse"]  # user's group survives


def test_v1_hook_key_same_sync_disable_removes_only_ours(tmp_path: Path):
    """Agentry-side half of the above: disabling in the same sync that migrates the v1 record
    removes agentry's identical element (no dead hook left) and drops the record."""
    from agentry.manifest import load_manifest

    proj, store, settings = _v1_hook_with_user_addition(tmp_path)
    _disable(store, "s/hook/guard")
    sync(proj)
    assert _read_json(settings)["hooks"]["PreToolUse"] == [_USER_GROUP]
    assert not [m for m in load_manifest(proj).merges if m.component == "s/hook/guard"]


def test_v1_hook_key_with_user_additions_migrated_then_disabled(tmp_path: Path):
    """With a sync between the upgrade and the disable, the migrated record claims only
    `agentry-guard`: the user's group survives both syncs, and disable removes only ours."""
    proj, store, settings = _v1_hook_with_user_addition(tmp_path)

    sync(proj)  # migrates the v1 record (claims only the identical element)
    assert _USER_GROUP in _read_json(settings)["hooks"]["PreToolUse"]
    _disable(store, "s/hook/guard")
    sync(proj)
    assert _read_json(settings)["hooks"]["PreToolUse"] == [_USER_GROUP]


def test_v1_hook_key_unchanged_is_owned_and_removed(tmp_path: Path):
    """Control: a v1-claimed hook key whose array still equals exactly what the component
    installs is provably agentry's, so disable removes it."""
    src = tmp_path / "src"
    _native_hook(src, "guard", "PreToolUse", "agentry-guard")
    proj = tmp_path / "proj"
    store = _project(proj, src, Component(source="s", type=_C.HOOK, name="guard"))
    sync(proj)
    _downgrade_manifest_to_v1(proj)

    _disable(store, "s/hook/guard")
    sync(proj)
    assert _hook_commands(proj, "PreToolUse") == []


def test_unproven_v1_key_never_removed(tmp_path: Path, local_source: Path):
    """A v1 record claims `github` while the key is absent on disk. After migration the user
    adds their own `github`: sync keeps it (W2/drift) and disable leaves it byte-identical."""
    proj, store = _mcp_project(tmp_path, local_source)
    sync(proj)
    _downgrade_manifest_to_v1(proj)
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    sync(proj)  # migrates the v1 record while the key is absent

    mine = json.dumps({"mcpServers": {"github": {"command": "MINE"}}})
    cfg.write_text(mine, encoding="utf-8")
    sync(proj)
    assert cfg.read_text(encoding="utf-8") == mine
    assert all(r.state != "ok" for r in _mcp_rows(proj))

    _disable(store, "s/mcp/github")
    sync(proj)
    assert cfg.read_text(encoding="utf-8") == mine


def test_unproven_v1_key_migrated_while_disabled_never_removed(tmp_path: Path, local_source: Path):
    """R1 risk path: the v1 record migrates (no proof, fp=None) while its component is
    disabled and the key is absent. A `github` the user adds later is never removed."""
    proj, store = _mcp_project(tmp_path, local_source)
    sync(proj)
    _downgrade_manifest_to_v1(proj)
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    _disable(store, "s/mcp/github")
    sync(proj)

    mine = json.dumps({"mcpServers": {"github": {"command": "MINE"}}})
    cfg.write_text(mine, encoding="utf-8")
    sync(proj)
    assert cfg.read_text(encoding="utf-8") == mine


def test_v1_claim_on_differing_user_value_not_adopted(tmp_path: Path, local_source: Path):
    """A v1 record claims `github`, but the on-disk value is not what the component installs
    (the user replaced it before the first v2 sync). With no proof of authorship the value
    is the user's: sync must not overwrite it, and disable must not remove it."""
    proj, store = _mcp_project(tmp_path, local_source)
    sync(proj)
    _downgrade_manifest_to_v1(proj)
    cfg = proj / ".mcp.json"
    mine = json.dumps({"mcpServers": {"github": {"command": "MINE"}}})
    cfg.write_text(mine, encoding="utf-8")

    sync(proj)
    assert cfg.read_text(encoding="utf-8") == mine
    _disable(store, "s/mcp/github")
    sync(proj)
    assert cfg.read_text(encoding="utf-8") == mine


def test_link_merge_move_blocked_by_unmanaged_file_keeps_old_install(tmp_path: Path):
    """When a dest-template change moves a link+merge link onto a path occupied by an
    unmanaged file, nothing is half-done: the user's file is untouched, and the old link and
    its manifest record both survive (no FileExistsError half-state)."""
    from agentry.manifest import load_manifest

    src = tmp_path / "src"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (src / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "r"}]}]}}),
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
      dest: ".claude/hooks/old/{{name}}"
      file: .claude/settings.json
      pointer: hooks
""",
        encoding="utf-8",
    )
    sync(proj)
    old_link = proj / ".claude/hooks/old/hooks"
    assert old_link.is_symlink()

    cfg = proj / ".agentry.yml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(".claude/hooks/old/", ".claude/hooks/new/"),
        encoding="utf-8",
    )
    blocker = proj / ".claude/hooks/new/hooks"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("user file\n", encoding="utf-8")

    sync(proj)  # must not raise

    assert blocker.read_text(encoding="utf-8") == "user file\n"
    assert old_link.is_symlink()
    assert [r.link_path for r in load_manifest(proj).link_merges] == [".claude/hooks/old/hooks"]
    assert "Stop" in _read_json(proj / ".claude/settings.json")["hooks"]


def _link_merge_hook_project(tmp_path: Path) -> tuple[Path, Path]:
    """A claude link+merge hook profile whose merged commands are rewritten to the link."""
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
    return proj, src


def test_v1_link_merge_hook_disable_does_not_leave_silent_dead_commands(tmp_path: Path):
    """Disabling a link+merge hook recorded by v1 re-derives the rewritten fragment, so the
    merged commands go together with the symlink — no dead command pointing at a removed
    script dir."""
    proj, _ = _link_merge_hook_project(tmp_path)
    sync(proj)
    link = proj / ".claude/hooks/agentry/hooks"
    settings = proj / ".claude/settings.json"
    assert link.is_symlink() and "Stop" in _read_json(settings)["hooks"]
    _downgrade_manifest_to_v1(proj)

    store = ConfigStore.load(proj)
    _disable(store, "s/hook/hooks")
    res = sync(proj)

    assert not link.exists() and not link.is_symlink()
    assert "Stop" not in _read_json(settings).get("hooks", {})
    assert not any("installed by an older agentry with link+merge" in w for w in res.warnings)


def test_v1_link_merge_hook_unresolvable_is_left_in_place_with_warning(tmp_path: Path):
    """W6: when a v1 link+merge record's fragment can't be re-derived (its source artifact
    is gone), agentry can't prove the merged entry is its own — it stays, loudly."""
    import shutil

    proj, src = _link_merge_hook_project(tmp_path)
    sync(proj)
    settings = proj / ".claude/settings.json"
    before = _read_json(settings)["hooks"]["Stop"]
    _downgrade_manifest_to_v1(proj)

    shutil.rmtree(src / "hooks")
    store = ConfigStore.load(proj)
    _disable(store, "s/hook/hooks")
    res = sync(proj)

    assert _read_json(settings)["hooks"]["Stop"] == before
    assert any(
        "installed by an older agentry with link+merge" in w and "left in place on removal" in w
        for w in res.warnings
    )


def test_v1_key_equal_to_desired_migrates_owned_and_removes_cleanly(
    tmp_path: Path, local_source: Path
):
    """Happy path: a v1-claimed named key whose value equals what the component installs is
    provably agentry's — migration owns it (v2 fingerprint), and disable removes exactly it
    with no 'left in place' warning, leaving the user's other keys alone."""
    from agentry.installers.merge import fingerprint
    from agentry.manifest import load_manifest

    proj, store = _mcp_project(tmp_path, local_source)
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}), encoding="utf-8")
    sync(proj)
    _downgrade_manifest_to_v1(proj)

    res = sync(proj)
    assert not any("older agentry" in w for w in res.warnings)
    (rec,) = [m for m in load_manifest(proj).merges if m.file == ".mcp.json"]
    assert rec.entries["github"].fingerprint == fingerprint(_GH)
    assert [r.state for r in _mcp_rows(proj)] == ["ok"]

    _disable(store, "s/mcp/github")
    res = sync(proj)
    assert not any("left in place" in w for w in res.warnings)
    assert _read_json(cfg)["mcpServers"] == {"mine": {"command": "x"}}


def test_unproven_v1_entry_note_names_older_agentry(tmp_path: Path, local_source: Path):
    """PM: the status/doctor note for an unproven v1 entry says it was recorded by an older
    agentry — never the bare generic 'run sync' hint, which can't fix it."""
    from agentry.doctor import run_checks

    proj, _ = _mcp_project(tmp_path, local_source)
    sync(proj)
    _downgrade_manifest_to_v1(proj)
    (proj / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "MINE"}}}), encoding="utf-8"
    )
    sync(proj)

    (row,) = _mcp_rows(proj)
    assert row.state != "ok" and row.note and "recorded by an older agentry" in row.note
    (msg,) = [
        c.message for c in run_checks(proj) if c.category == "drift" and "s/mcp/github" in c.message
    ]
    assert "recorded by an older agentry" in msg
    assert "(run `agentry sync`)" not in msg


def test_update_with_v1_manifest_advises_plain_sync_first(tmp_path: Path, local_source: Path):
    """`sync(update=True)` on a v1 manifest warns once to run a plain sync first; a v2
    manifest gets no such advisory."""
    advisory = "manifest from an older agentry"
    proj, _ = _mcp_project(tmp_path, local_source)
    sync(proj)
    res = sync(proj, update=True)
    assert not any(advisory in w for w in res.warnings)  # v2: silent

    _downgrade_manifest_to_v1(proj)
    res = sync(proj, update=True)
    assert sum(advisory in w for w in res.warnings) == 1


def test_doctor_strict_exits_1_on_unproven_v1_drift(
    tmp_path: Path, local_source: Path, monkeypatch: pytest.MonkeyPatch
):
    """PM ruling: an unproven v1 entry is not exempt from `doctor --strict`."""
    from typer.testing import CliRunner

    from agentry.cli import app

    proj, _ = _mcp_project(tmp_path, local_source)
    sync(proj)
    _downgrade_manifest_to_v1(proj)
    (proj / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "MINE"}}}), encoding="utf-8"
    )
    sync(proj)
    monkeypatch.chdir(proj)
    assert CliRunner().invoke(app, ["doctor", "--strict"]).exit_code == 1


def _drift_lines(proj: Path, ref: str) -> list[str]:
    from agentry.doctor import run_checks

    return [
        c.message
        for c in run_checks(proj)
        if c.level == "warn" and c.category == "drift" and f"(from {ref} " in c.message
    ]


def test_doctor_lists_each_hand_edited_entry_per_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """R5: doctor names every merge entry agentry can't vouch for, one line per key, with
    its file, pointer.key, owning ref and the delete-or-disable action; --strict fails."""
    from typer.testing import CliRunner

    from agentry.cli import app

    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "servers.json").write_text(
        json.dumps({"github": {"command": "gh"}, "gitlab": {"command": "gl"}}), encoding="utf-8"
    )
    proj = tmp_path / "proj"
    _project(proj, src, Component(source="s", type=_C.MCP, name="servers"))
    sync(proj)
    cfg = proj / ".mcp.json"
    doc = _read_json(cfg)
    doc["mcpServers"]["github"] = {"command": "gh-edited"}
    doc["mcpServers"]["gitlab"] = {"command": "gl-edited"}
    cfg.write_text(json.dumps(doc), encoding="utf-8")

    lines = _drift_lines(proj, "s/mcp/servers")
    assert len(lines) == 2
    from agentry.doctor import run_checks

    # The per-entry lines replace the row-level "<ref> → <target>: drift" line.
    assert not any(c.message.startswith("s/mcp/servers → claude:") for c in run_checks(proj))
    for key in ("github", "gitlab"):
        (line,) = [m for m in lines if f"'mcpServers.{key}'" in m]
        assert line.startswith(".mcp.json: ")
        assert "was edited by hand" in line
        assert f"run `agentry keep s/mcp/servers {key}` to keep yours" in line

    monkeypatch.chdir(proj)
    assert CliRunner().invoke(app, ["doctor", "--strict"]).exit_code == 1


def test_doctor_lists_unproven_v1_entry_per_key(tmp_path: Path, local_source: Path):
    """R5: an unproven v1 entry gets its own key-level line saying an older agentry
    recorded it."""
    proj, _ = _mcp_project(tmp_path, local_source)
    sync(proj)
    _downgrade_manifest_to_v1(proj)
    (proj / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "MINE"}}}), encoding="utf-8"
    )
    sync(proj)

    (line,) = _drift_lines(proj, "s/mcp/github")
    assert "'mcpServers.github'" in line
    assert "was recorded by an older agentry and differs from the current source" in line
    assert "run `agentry keep s/mcp/github github` to keep yours" in line


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
