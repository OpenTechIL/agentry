"""T13: per-component `keep:` — merged keys the user owns; agentry never writes or removes
them (ADR-001 "keep"). Assertions are on user-visible files, status and CLI output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import status, sync

_C = ComponentType
_REF = "s/mcp/servers"
runner = CliRunner()


def _project(
    tmp_path: Path, keep: list[str] | None = None, targets=(Target.CLAUDE,)
) -> tuple[Path, ConfigStore]:
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "servers.json").write_text(
        json.dumps({"github": {"command": "gh"}, "gitlab": {"command": "gl"}}), encoding="utf-8"
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, list(targets)).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.MCP, name="servers", keep=keep or []))
    store.save()
    return proj, store


def _servers(proj: Path, file: str = ".mcp.json", pointer: str = "mcpServers") -> dict:
    path = proj / file
    return json.loads(path.read_text(encoding="utf-8")).get(pointer, {}) if path.is_file() else {}


def _set_keep(proj: Path, keep: list[str]) -> None:
    store = ConfigStore.load(proj)
    for c in store.doc["components"]:
        if c["name"] == "servers":
            c["keep"] = keep
    store.save()


def _edit_github(proj: Path) -> bytes:
    cfg = proj / ".mcp.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    doc["mcpServers"]["github"] = {"command": "MINE"}
    cfg.write_text(json.dumps(doc), encoding="utf-8")
    return cfg.read_bytes()


def test_kept_key_survives_sync_update_and_disable(tmp_path: Path):
    proj, store = _project(tmp_path)
    sync(proj)
    _edit_github(proj)
    _set_keep(proj, ["github"])

    res = sync(proj)
    assert _servers(proj)["github"] == {"command": "MINE"}
    assert not any("github" in w for w in res.warnings)  # no W2 for a kept key

    res = sync(proj, update=True)
    assert _servers(proj)["github"] == {"command": "MINE"}
    assert not any("github" in w for w in res.warnings)

    store = ConfigStore.load(proj)
    store.set_enabled(_REF, False)
    store.save()
    res = sync(proj)
    assert _servers(proj) == {"github": {"command": "MINE"}}  # gitlab (ours) removed
    assert not any("left in place" in w for w in res.warnings)  # no W4 for a kept key


def test_kept_key_absent_on_disk_is_not_created(tmp_path: Path):
    proj, _ = _project(tmp_path, keep=["github"])
    sync(proj)
    assert set(_servers(proj)) == {"gitlab"}


def test_status_shows_ok_kept_and_doctor_strict_passes(tmp_path: Path, monkeypatch):
    proj, _ = _project(tmp_path)
    sync(proj)
    _edit_github(proj)
    _set_keep(proj, ["github"])
    sync(proj)

    rows, _ = status(proj)
    (row,) = [r for r in rows if r.where.startswith(".mcp.json")]
    assert row.state == "ok (kept)"
    assert not [c for c in run_checks(proj) if c.category == "drift"]
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["doctor", "--strict"]).exit_code == 0


def test_target_qualified_keep_affects_only_that_target(tmp_path: Path):
    proj, _ = _project(tmp_path, keep=["claude:github"], targets=(Target.CLAUDE, Target.COPILOT))
    sync(proj)
    assert set(_servers(proj)) == {"gitlab"}  # claude: github kept (not created)
    assert set(_servers(proj, ".vscode/mcp.json", "servers")) == {"github", "gitlab"}


def test_removing_a_keep_entry_reclaims_nothing_until_user_deletes_it(tmp_path: Path):
    proj, _ = _project(tmp_path)
    sync(proj)
    mine = _edit_github(proj)
    _set_keep(proj, ["github"])
    sync(proj)

    _set_keep(proj, [])
    res = sync(proj)
    assert (proj / ".mcp.json").read_bytes() == mine  # still the user's (W1b, refused)
    assert any("github" in w for w in res.warnings)

    cfg = proj / ".mcp.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    del doc["mcpServers"]["github"]
    cfg.write_text(json.dumps(doc), encoding="utf-8")
    sync(proj)
    assert _servers(proj)["github"] == {"command": "gh"}  # re-created and owned again


def test_keep_typo_warns_once_per_sync(tmp_path: Path):
    proj, _ = _project(tmp_path, keep=["githb"])
    for _ in range(2):
        res = sync(proj)
        hits = [w for w in res.warnings if "keep entry 'githb' matches no key" in w]
        assert len(hits) == 1


@pytest.mark.parametrize("bad", ["", ":x", "x:", " github"])
def test_malformed_keep_entry_is_rejected(bad: str):
    with pytest.raises(ValueError):
        Component(source="s", type=_C.MCP, name="servers", keep=[bad])


def test_keep_cli_appends_preserves_comments_and_syncs(tmp_path: Path, monkeypatch):
    proj, _ = _project(tmp_path)
    sync(proj)
    _edit_github(proj)
    cfg = proj / ".agentry.yml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "# trailing user comment\n", encoding="utf-8")
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["keep", _REF, "github"])
    assert result.exit_code == 0, result.output
    text = cfg.read_text(encoding="utf-8")
    assert "# trailing user comment" in text
    assert "# agentry — AI agent dependencies for this project." in text
    (comp,) = ConfigStore.load(proj).parsed().components
    assert comp.keep == ["github"]
    rows, _ = status(proj)
    assert [r.state for r in rows if r.where.startswith(".mcp.json")] == ["ok (kept)"]

    again = runner.invoke(app, ["keep", _REF, "github"])
    assert again.exit_code == 0
    assert "already keeps" in again.output
    assert ConfigStore.load(proj).parsed().components[0].keep == ["github"]


def test_keep_cli_unknown_ref_exits_1(tmp_path: Path, monkeypatch):
    proj, _ = _project(tmp_path)
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["keep", "s/mcp/nope", "github"]).exit_code == 1


def test_why_lists_keep_entries(tmp_path: Path, monkeypatch):
    proj, _ = _project(tmp_path, keep=["github"])
    sync(proj)
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["why", _REF])
    assert result.exit_code == 0, result.output
    assert "keeps" in result.output and "github" in result.output


def test_sync_with_keep_is_idempotent(tmp_path: Path):
    proj, _ = _project(tmp_path)
    sync(proj)
    _edit_github(proj)
    _set_keep(proj, ["github"])
    sync(proj)
    before = (proj / ".mcp.json").read_bytes()

    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert (proj / ".mcp.json").read_bytes() == before


def test_unkeep_refuses_only_the_differing_key_and_applies_sibling_updates(tmp_path: Path):
    """After un-keep, a differing user value refuses only that key; an upstream change to a
    sibling key of the same component is still applied."""
    proj, _ = _project(tmp_path)
    sync(proj)
    mine = {"command": "MINE"}
    _edit_github(proj)
    _set_keep(proj, ["github"])
    sync(proj)

    _set_keep(proj, [])
    src = tmp_path / "src" / "mcp" / "servers.json"
    src.write_text(
        json.dumps({"github": {"command": "gh"}, "gitlab": {"command": "gl-v2"}}),
        encoding="utf-8",
    )
    sync(proj)

    servers = _servers(proj)
    assert servers["github"] == mine  # refused: still the user's
    assert servers["gitlab"] == {"command": "gl-v2"}  # sibling upstream change applied


# --- PR1 atomicity guards: Rev 4 per-key refusal must not go further than this ------------


def test_first_install_w1_refuses_whole_component(tmp_path: Path, monkeypatch):
    """On a component's first install into a destination, one key colliding with a
    hand-written entry refuses the whole component: none of its keys are written."""
    proj, _ = _project(tmp_path)
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"github": {"command": "MINE"}}}), encoding="utf-8")
    before = cfg.read_bytes()

    res = sync(proj)
    assert any("not added by agentry" in w and f"{_REF} was not merged" in w for w in res.warnings)
    assert cfg.read_bytes() == before  # gitlab (no collision) not written either
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["doctor", "--strict"]).exit_code == 1


def test_w3_non_object_section_refuses_whole_destination_after_install(tmp_path: Path, monkeypatch):
    """Even after install, a section the user turned into a non-object refuses the whole
    destination: no sibling update is applied into it."""
    proj, _ = _project(tmp_path)
    sync(proj)
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": ["user", "list"]}), encoding="utf-8")
    before = cfg.read_bytes()
    (tmp_path / "src" / "mcp" / "servers.json").write_text(
        json.dumps({"github": {"command": "gh-v2"}, "gitlab": {"command": "gl-v2"}}),
        encoding="utf-8",
    )

    res = sync(proj)
    assert any("is a list, not an object" in w for w in res.warnings)
    assert cfg.read_bytes() == before
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["doctor", "--strict"]).exit_code == 1


# --- ADR-001 Rev 4: after install, W1/W1b/W5 refuse only the offending key ---------------


def _upstream(tmp_path: Path, servers: dict) -> None:
    (tmp_path / "src" / "mcp" / "servers.json").write_text(json.dumps(servers), encoding="utf-8")


def _strict_exit(proj: Path, monkeypatch) -> int:
    monkeypatch.chdir(proj)
    return runner.invoke(app, ["doctor", "--strict"]).exit_code


def test_installed_component_new_upstream_key_collides_refused_per_key(tmp_path: Path, monkeypatch):
    """PM Rev 4 #1: an upgrade adds a key the user already hand-wrote. Only that key is
    refused (W1); the component's other keys still update in the same sync."""
    proj, _ = _project(tmp_path)
    sync(proj)
    cfg = proj / ".mcp.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    doc["mcpServers"]["slack"] = {"command": "MINE"}
    cfg.write_text(json.dumps(doc), encoding="utf-8")
    _upstream(
        tmp_path,
        {"github": {"command": "gh"}, "gitlab": {"command": "gl-v2"}, "slack": {"command": "sl"}},
    )

    res = sync(proj)
    servers = _servers(proj)
    assert servers["slack"] == {"command": "MINE"}  # refused
    assert servers["gitlab"] == {"command": "gl-v2"}  # sibling updated
    assert any("'slack' was not merged into claude" in w for w in res.warnings)
    rows, _ = status(proj)
    assert [r.state for r in rows if r.where.startswith(".mcp.json")] == ["drift"]
    assert _strict_exit(proj, monkeypatch) == 1


def test_installed_component_duplicate_claim_refused_per_key(tmp_path: Path, monkeypatch):
    """PM Rev 4 #2: after install, an upstream key that another component already owns is
    refused for that key only (W5); siblings still update. Under Rev 6 the earlier-listed
    claimant wins, so `extra` is listed first to keep it the owner."""
    proj, store = _project(tmp_path)
    (tmp_path / "src" / "mcp" / "extra.json").write_text(
        json.dumps({"slack": {"command": "from-extra"}}), encoding="utf-8"
    )
    store = ConfigStore.load(proj)
    store.doc["components"].insert(
        0,
        Component(source="s", type=_C.MCP, name="extra").model_dump(mode="json", exclude_none=True),
    )
    store.save()
    sync(proj)
    _upstream(
        tmp_path,
        {"github": {"command": "gh"}, "gitlab": {"command": "gl-v2"}, "slack": {"command": "sl"}},
    )

    res = sync(proj)
    servers = _servers(proj)
    assert servers["slack"] == {"command": "from-extra"}  # owner unchanged
    assert servers["gitlab"] == {"command": "gl-v2"}  # sibling updated
    assert any(w.startswith(_REF) and "is already provided by" in w for w in res.warnings)
    assert _strict_exit(proj, monkeypatch) == 1


def test_installed_duplicate_claim_transfers_to_earlier_listed(tmp_path: Path):
    """Rev 6 sibling of the test above: `servers` is listed before `extra`, so when its
    upstream adds `slack`, ownership transfers from `extra` in one sync (fingerprint-safe),
    `extra` gets W5, and the transfer line is reported exactly once."""
    proj, store = _project(tmp_path)
    (tmp_path / "src" / "mcp" / "extra.json").write_text(
        json.dumps({"slack": {"command": "from-extra"}}), encoding="utf-8"
    )
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.MCP, name="extra"))  # after servers
    store.save()
    sync(proj)
    assert _servers(proj)["slack"] == {"command": "from-extra"}  # extra owns it for now
    _upstream(
        tmp_path,
        {"github": {"command": "gh"}, "gitlab": {"command": "gl-v2"}, "slack": {"command": "sl"}},
    )

    res = sync(proj)
    servers = _servers(proj)
    assert servers["slack"] == {"command": "sl"}  # transferred to the earlier-listed claimant
    assert servers["gitlab"] == {"command": "gl-v2"}
    line = (
        ".mcp.json:mcpServers.slack now comes from s/mcp/servers (listed earlier in "
        ".agentry.yml) instead of s/mcp/extra — to keep s/mcp/extra's version, list it before "
        "s/mcp/servers in .agentry.yml, or disable s/mcp/servers"
    )
    assert res.updated.count(line) == 1
    assert any(
        w.startswith("s/mcp/extra") and "already provided by s/mcp/servers" in w
        for w in res.warnings
    )
    assert line not in sync(proj).updated  # reported once, not every sync


def test_kept_key_survives_other_claimant_disable(tmp_path: Path):
    """A keeps k; B also ships k and is refused; disabling B leaves k untouched."""
    proj, _ = _project(tmp_path)
    sync(proj)
    mine = _edit_github(proj)
    _set_keep(proj, ["github"])
    sync(proj)

    (tmp_path / "src" / "mcp" / "dup.json").write_text(
        json.dumps({"github": {"command": "from-dup"}}), encoding="utf-8"
    )
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.MCP, name="dup"))
    store.save()
    res = sync(proj)
    assert any(w.startswith("s/mcp/dup") and "github" in w for w in res.warnings)
    assert (proj / ".mcp.json").read_bytes() == mine

    store = ConfigStore.load(proj)
    store.set_enabled("s/mcp/dup", False)
    store.save()
    sync(proj)
    assert (proj / ".mcp.json").read_bytes() == mine


# --- keep CLI: target validation and literal output -------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_keep_cli_unknown_target_exits_1_with_message(tmp_path: Path, monkeypatch):
    proj, _ = _project(tmp_path, targets=(Target.CLAUDE, Target.COPILOT))
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["keep", _REF, "nope:github"])
    assert result.exit_code == 1
    assert _norm(
        f"Unknown target 'nope' for {_REF} — it installs into: claude, copilot. Use "
        "'<target>:<key>' with one of those, or a bare '<key>' for all of them."
    ) in _norm(result.stderr)
    assert ConfigStore.load(proj).parsed().components[0].keep == []


@pytest.mark.parametrize("arg", ["claude:b:c", "[bold]x"])
def test_keep_cli_and_typo_warning_print_user_text_literally(tmp_path: Path, monkeypatch, arg):
    """User text is never interpreted as Rich markup or emoji codes (`:b:`, `[bold]`)."""
    proj, _ = _project(tmp_path)
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["keep", _REF, arg])
    assert result.exit_code == 0, result.output
    assert f"Keeping {arg} for {_REF}" in _norm(result.output)
    assert "\N{NEGATIVE SQUARED LATIN CAPITAL LETTER B}" not in result.output

    again = runner.invoke(app, ["sync"])
    assert f"keep entry '{arg}'" in _norm(again.stderr)


# --- keep hint names only the drifted targets (PM rule, ADR-001) ------------------------


def _edit(proj: Path, file: str, pointer: str, key: str) -> None:
    cfg = proj / file
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    doc[pointer][key] = {"command": "MINE"}
    cfg.write_text(json.dumps(doc), encoding="utf-8")


def _github_hints(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "'mcpServers.github'" in w or "'servers.github'" in w]


def test_keep_hint_names_the_one_drifted_target(tmp_path: Path):
    proj, _ = _project(tmp_path, targets=(Target.CLAUDE, Target.COPILOT))
    sync(proj)
    _edit(proj, ".mcp.json", "mcpServers", "github")

    hints = _github_hints(sync(proj).warnings)
    assert hints and all(f"`agentry keep {_REF} claude:github`" in w for w in hints)


def test_keep_hint_is_bare_when_every_target_drifted(tmp_path: Path):
    proj, _ = _project(tmp_path, targets=(Target.CLAUDE, Target.COPILOT))
    sync(proj)
    _edit(proj, ".mcp.json", "mcpServers", "github")
    _edit(proj, ".vscode/mcp.json", "servers", "github")

    hints = _github_hints(sync(proj).warnings)
    assert len(hints) == 2
    assert all(f"`agentry keep {_REF} github`" in w for w in hints)


# --- kept but missing: reported, never created (PM, ADR-001) ----------------------------

_KEPT_MISSING = "kept, but no entry on {target}"


def _kept_missing_checks(proj: Path, target: str, monkeypatch) -> None:
    rows, _ = status(proj)
    (row,) = [r for r in rows if r.target == target]
    assert row.state == "missing"
    assert row.note and _KEPT_MISSING.format(target=target) in row.note
    assert "remove it from `keep:`" in row.note and "<target>:<key>" in row.note
    assert any(_KEPT_MISSING.format(target=target) in c.message for c in run_checks(proj))
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["doctor", "--strict"]).exit_code == 1


def test_bare_keep_new_target_reports_missing_and_writes_nothing(tmp_path: Path, monkeypatch):
    """A bare `keep: [github]`, then a target added later: agentry never creates the kept
    key there, but reports the gap."""
    proj, _ = _project(tmp_path)
    sync(proj)
    _edit_github(proj)
    _set_keep(proj, ["github"])
    sync(proj)

    store = ConfigStore.load(proj)
    store.doc["targets"].append("copilot")
    store.save()
    res = sync(proj)

    assert "github" not in _servers(proj, ".vscode/mcp.json", "servers")
    assert any(_KEPT_MISSING.format(target="copilot") in w for w in res.warnings)
    _kept_missing_checks(proj, "copilot", monkeypatch)


def test_deleted_kept_entry_reports_missing_and_is_not_recreated(tmp_path: Path, monkeypatch):
    proj, _ = _project(tmp_path)
    sync(proj)
    _edit_github(proj)
    _set_keep(proj, ["github"])
    sync(proj)

    cfg = proj / ".mcp.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    del doc["mcpServers"]["github"]
    cfg.write_text(json.dumps(doc), encoding="utf-8")
    res = sync(proj)

    assert "github" not in _servers(proj)
    assert any(_KEPT_MISSING.format(target="claude") in w for w in res.warnings)
    _kept_missing_checks(proj, "claude", monkeypatch)


# --- config round-trip -------------------------------------------------------------------


def test_add_component_persists_transform(tmp_path: Path):
    """Regression: ConfigStore.add_component dropped `transform:` on save."""
    from agentry.models import ComponentTransform

    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(tmp_path)))
    store.add_component(
        Component(
            source="s",
            type=_C.COMMAND,
            name="ship",
            transform=ComponentTransform(provider="agent", prompt="Rewrite for Gemini"),
        )
    )
    store.save()

    (comp,) = ConfigStore.load(proj).parsed().components
    assert comp.transform is not None
    assert comp.transform.provider == "agent"
    assert comp.transform.prompt == "Rewrite for Gemini"


# --- the "kept" warning kind summarizes like T14 -----------------------------------------

_KEPT_SUMMARY = (
    "  ! 2 keys in keep: have no entry on one or more targets and were not installed — run "
    "`agentry doctor` for the list"
)


def _sync_warn_lines(proj: Path, monkeypatch, *args: str) -> list[str]:
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["sync", *args])
    assert result.exit_code == 0, result.output
    return [ln for ln in result.stderr.splitlines() if ln.startswith("  ! ")]


def test_single_kept_missing_warning_prints_in_full(tmp_path: Path, monkeypatch):
    proj, _ = _project(tmp_path, keep=["github"])
    (full,) = [w for w in sync(proj).warnings if _KEPT_MISSING.format(target="claude") in w]
    lines = _sync_warn_lines(proj, monkeypatch)
    assert f"  ! {full}" in lines
    assert _KEPT_SUMMARY not in lines


def test_repeated_kept_missing_warnings_collapse_to_one_summary(tmp_path: Path, monkeypatch):
    proj, _ = _project(tmp_path, keep=["github", "gitlab"])
    full = [w for w in sync(proj).warnings if _KEPT_MISSING.format(target="claude") in w]
    assert len(full) == 2

    lines = _sync_warn_lines(proj, monkeypatch)
    assert lines.count(_KEPT_SUMMARY) == 1
    assert not any(f"  ! {w}" in lines for w in full)

    verbose = _sync_warn_lines(proj, monkeypatch, "--verbose")
    assert all(f"  ! {w}" in verbose for w in full) and _KEPT_SUMMARY not in verbose
