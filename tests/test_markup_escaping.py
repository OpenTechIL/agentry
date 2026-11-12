"""T15: user-controlled text is printed literally by every CLI surface.

Rich interprets ``[...]`` as markup and (unless disabled) ``:name:`` as emoji, so a source,
component, path or error containing them could lose text or restyle output. Filesystem
names here avoid ``:`` (illegal on Windows); ``:b:`` is exercised through a merged entry key
and CLI-argument refs, which never become paths. Each command
here is run on names built from those characters and must print them verbatim — while
agentry's own styling tags never leak as literal text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app

runner = CliRunner()

SRC = "[red]s"  # source name
SKILL = "[bold]x"  # component name with markup (a directory name: no ":" — illegal on Windows)
MCP = "[i]srv"  # merged component name with markup
KEY = "[/]k:b:"  # merged entry key: a bare closing tag + an emoji shortcode (never a filename)
REF = f"{SRC}/skill/{SKILL}"
B_EMOJI = "\N{NEGATIVE SQUARED LATIN CAPITAL LETTER B}"


def _norm(text: str) -> str:
    return " ".join(text.split())


def _out(result) -> str:
    return _norm(result.output + (result.stderr or ""))


def _assert_literal(result, *needles: str) -> None:
    text = _out(result)
    for needle in needles:
        assert needle in text, f"{needle!r} not printed literally in: {text}"
    assert B_EMOJI not in text


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    src = tmp_path / "src[/]dir"
    (src / "skills" / SKILL).mkdir(parents=True)
    (src / "skills" / SKILL / "SKILL.md").write_text("# x\n", encoding="utf-8")
    (src / "mcp").mkdir()
    (src / "mcp" / f"{MCP}.json").write_text(
        json.dumps({KEY: {"command": "npx"}}), encoding="utf-8"
    )
    p = tmp_path / "proj"
    p.mkdir()
    monkeypatch.chdir(p)
    r = runner.invoke(
        app, ["init", "--target", "claude", "--target", "cursor", "--no-default-catalog"]
    )
    assert r.exit_code == 0, r.output
    p.joinpath("_src").write_text(str(src), encoding="utf-8")
    return p


def _src(proj: Path) -> str:
    return proj.joinpath("_src").read_text(encoding="utf-8")


def _add_source(proj: Path):
    r = runner.invoke(app, ["source", "add", SRC, _src(proj), "--local"])
    assert r.exit_code == 0, r.output
    return r


def test_source_add_and_list_print_names_and_paths_literally(proj: Path):
    added = _add_source(proj)
    _assert_literal(added, f"Added source {SRC}", "src[/]dir")

    listed = runner.invoke(app, ["source", "list"])
    assert listed.exit_code == 0, listed.output
    _assert_literal(listed, SRC)


def test_add_sync_status_list_why_print_component_names_literally(proj: Path):
    _add_source(proj)
    added = runner.invoke(app, ["add", REF])
    assert added.exit_code == 0, added.output
    # "+ link" line and the cursor skip warning both carry the name
    _assert_literal(added, f"Added {REF}", f".claude/skills/{SKILL}", f"{REF}: target 'cursor'")

    synced = runner.invoke(app, ["sync", "--verbose"])
    assert synced.exit_code == 0, synced.output
    _assert_literal(synced, REF)

    status = runner.invoke(app, ["status"])
    assert status.exit_code == 0, status.output
    _assert_literal(status, SKILL)

    listed = runner.invoke(app, ["list"])
    assert listed.exit_code == 0, listed.output
    _assert_literal(listed, SKILL)

    why = runner.invoke(app, ["why", REF])
    assert why.exit_code == 0, why.output
    _assert_literal(why, REF, SRC)


def test_merged_entry_names_print_literally_in_warnings_and_doctor(proj: Path):
    """A hand-edited merged entry whose key and component contain markup: sync warning,
    status note and doctor line all keep it verbatim."""
    from agentry.doctor import run_checks

    _add_source(proj)
    mcp_ref = f"{SRC}/mcp/{MCP}"
    assert runner.invoke(app, ["add", mcp_ref]).exit_code == 0
    cfg = proj / ".mcp.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    doc["mcpServers"][KEY] = {"command": "MINE"}
    cfg.write_text(json.dumps(doc), encoding="utf-8")

    synced = runner.invoke(app, ["sync"])
    _assert_literal(synced, f"'mcpServers.{KEY}'", mcp_ref)

    status = runner.invoke(app, ["status"])
    _assert_literal(status, KEY)

    doctor = runner.invoke(app, ["doctor"])
    _assert_literal(doctor, f"'mcpServers.{KEY}'", mcp_ref)
    assert any(KEY in c.message for c in run_checks(proj))


def test_error_path_prints_user_text_literally(proj: Path):
    result = runner.invoke(app, ["add", "[x]y:b:/skill/z"])
    assert result.exit_code == 1
    _assert_literal(result, "Unknown source '[x]y:b:'")

    why = runner.invoke(app, ["why", "[x]y:b:/skill/[bold]z"])
    assert why.exit_code == 1
    _assert_literal(why, "[x]y:b:/skill/[bold]z")


def test_catalog_and_target_list_print_names_literally(proj: Path, tmp_path: Path):
    from agentry.config import ConfigStore

    catalog = tmp_path / "cat[c].json"
    catalog.write_text(json.dumps({"repositories": {}}), encoding="utf-8")
    store = ConfigStore.load(proj)
    store.doc["repositories"] = [{"name": "[c]cat", "location": str(catalog)}]
    store.doc["targets"].append("[t]tool")
    store.doc["target_profiles"] = {
        "[t]tool": {"skill": {"strategy": "link", "dest": ".tool/skills/{name}"}}
    }
    store.save()

    catalogs = runner.invoke(app, ["catalog", "list"])
    assert catalogs.exit_code == 0, catalogs.output
    _assert_literal(catalogs, "[c]cat")

    targets = runner.invoke(app, ["target", "list"])
    assert targets.exit_code == 0, targets.output
    _assert_literal(targets, "[t]tool")


def test_our_own_styling_tags_never_leak(proj: Path):
    """Escaping user text must not escape agentry's own markup: no literal style tags."""
    _add_source(proj)
    runner.invoke(app, ["add", REF])
    for args in (["source", "list"], ["status"], ["list"], ["sync"], ["why", REF]):
        result = runner.invoke(app, args)
        text = result.output + (result.stderr or "")
        for tag in ("[dim]", "[/dim]", "[green]", "[/green]", "[yellow]", "[cyan]", "[/yellow]"):
            assert tag not in text, f"{args}: our tag {tag!r} printed literally"


def test_unlocked_source_shows_plain_dash_not_dim_tag(proj: Path):
    from agentry.config import ConfigStore
    from agentry.models import Source, SourceType

    store = ConfigStore.load(proj)
    store.add_source(Source(name=SRC, type=SourceType.LOCAL, path=_src(proj)))
    store.save()
    listed = runner.invoke(app, ["source", "list"])
    assert listed.exit_code == 0, listed.output
    row = next(ln for ln in listed.output.splitlines() if SRC in ln)
    assert "—" in row and "[dim]" not in row
