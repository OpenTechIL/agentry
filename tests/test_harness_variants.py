"""T16: a per-harness variant (`<base>-<target>`) replaces its canonical merge component on
that target only. The canonical still installs everywhere else; switching between them is a
single sync; hand-added entries are never touched; idempotency is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import status, sync

_C = ComponentType
runner = CliRunner()
_W5 = "is already provided by"


def _hook(command: str) -> dict:
    return {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]}}


def _write(src: Path, kind: str, name: str, payload: dict) -> None:
    (src / kind).mkdir(parents=True, exist_ok=True)
    (src / kind / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def _project(
    tmp_path: Path, targets: list[Target], ctype: ComponentType, *names: str
) -> tuple[Path, Path]:
    src = tmp_path / "src"
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, targets).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for n in names:
        store.add_component(Component(source="s", type=ctype, name=n))
    store.save()
    return proj, src


def _mcp(proj: Path, file: str) -> dict:
    path = proj / file
    return (
        json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {}) if path.is_file() else {}
    )


def _hook_cmds(proj: Path, file: str) -> list[str]:
    path = proj / file
    if not path.is_file():
        return []
    groups = json.loads(path.read_text(encoding="utf-8")).get("hooks", {}).get("SessionStart", [])
    return [h["command"] for g in groups for h in g["hooks"]]


def _mcp_pair(tmp_path: Path, *names: str) -> tuple[Path, Path]:
    proj, src = _project(tmp_path, [Target.CLAUDE, Target.CURSOR], _C.MCP, *names)
    _write(src, "mcp", "fs", {"fs": {"command": "canon"}})
    _write(src, "mcp", "fs-cursor", {"fs": {"command": "cur"}})
    return proj, src


def _set_enabled(proj: Path, ref: str, on: bool) -> None:
    store = ConfigStore.load(proj)
    store.set_enabled(ref, on)
    store.save()


# --- the variant replaces the canonical on its own target only --------------------------


def test_variant_replaces_canonical_on_its_target(tmp_path: Path):
    proj, _ = _mcp_pair(tmp_path, "fs", "fs-cursor")
    res = sync(proj)

    assert _mcp(proj, ".mcp.json") == {"fs": {"command": "canon"}}  # claude: canonical
    assert _mcp(proj, ".cursor/mcp.json") == {"fs": {"command": "cur"}}  # cursor: variant only
    assert not any(_W5 in w for w in res.warnings)  # canonical vs own variant is no conflict
    rows = {(r.ref, r.target) for r in status(proj)[0]}
    assert ("s/mcp/fs", "claude") in rows and ("s/mcp/fs-cursor", "cursor") in rows
    assert ("s/mcp/fs", "cursor") not in rows  # no row for the replaced canonical


def test_hook_variant_replaces_canonical_on_gemini(tmp_path: Path):
    proj, src = _project(tmp_path, [Target.CLAUDE, Target.GEMINI], _C.HOOK, "hooks", "hooks-gemini")
    _write(src, "hooks", "hooks", _hook("canon"))
    _write(src, "hooks", "hooks-gemini", _hook("gem"))
    res = sync(proj)

    assert _hook_cmds(proj, ".claude/settings.json") == ["canon"]
    assert _hook_cmds(proj, ".gemini/settings.json") == ["gem"]
    assert not any(_W5 in w for w in res.warnings)


# --- no variant in effect -> the canonical applies --------------------------------------


def test_canonical_applies_when_variant_absent(tmp_path: Path):
    proj, _ = _mcp_pair(tmp_path, "fs")
    sync(proj)
    assert _mcp(proj, ".cursor/mcp.json") == {"fs": {"command": "canon"}}


def test_canonical_still_applies_when_variant_disabled(tmp_path: Path):
    proj, _ = _mcp_pair(tmp_path, "fs", "fs-cursor")
    sync(proj)
    _set_enabled(proj, "s/mcp/fs-cursor", False)

    sync(proj)  # one sync switches cursor back to the canonical
    assert _mcp(proj, ".cursor/mcp.json") == {"fs": {"command": "canon"}}
    assert _mcp(proj, ".mcp.json") == {"fs": {"command": "canon"}}


def test_variant_from_other_source_does_not_suppress_canonical(tmp_path: Path):
    """Only a variant from the *same* source replaces the canonical; another source's
    `fs-cursor` is an unrelated component, so the shared key is a real W5 conflict."""
    proj, src = _mcp_pair(tmp_path, "fs")
    other = tmp_path / "other"
    _write(other, "mcp", "fs-cursor", {"fs": {"command": "other"}})
    store = ConfigStore.load(proj)
    store.add_source(Source(name="o", type=SourceType.LOCAL, path=str(other)))
    store.add_component(Component(source="o", type=_C.MCP, name="fs-cursor"))
    store.save()

    res = sync(proj)
    assert any(_W5 in w for w in res.warnings)
    rows = {(r.ref, r.target) for r in status(proj)[0]}
    assert ("s/mcp/fs", "cursor") in rows  # canonical still routed to cursor


# --- switching from the old both-merged state ------------------------------------------


def test_switching_to_variant_removes_canonical_entry_same_sync(tmp_path: Path):
    """Upgrade path: the canonical is installed on cursor (as before T16), the user has a
    hand-added entry there, then the variant is enabled. One sync leaves only the variant's
    value for the shared key, and the hand-added entry is untouched."""
    proj, _ = _mcp_pair(tmp_path, "fs", "fs-cursor")
    _set_enabled(proj, "s/mcp/fs-cursor", False)
    sync(proj)
    cfg = proj / ".cursor/mcp.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    doc["mcpServers"]["mine"] = {"command": "MINE"}
    cfg.write_text(json.dumps(doc), encoding="utf-8")

    _set_enabled(proj, "s/mcp/fs-cursor", True)
    res = sync(proj)
    assert _mcp(proj, ".cursor/mcp.json") == {
        "fs": {"command": "cur"},
        "mine": {"command": "MINE"},
    }
    assert not any(_W5 in w for w in res.warnings)
    assert _mcp(proj, ".mcp.json") == {"fs": {"command": "canon"}}  # claude unaffected


def test_why_shows_variant_replacing_canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj, _ = _mcp_pair(tmp_path, "fs", "fs-cursor")
    sync(proj)
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["why", "s/mcp/fs"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "cursor replaced by s/mcp/fs-cursor" in text
    assert "claude .mcp.json:mcpServers ok" in text

    variant = runner.invoke(app, ["why", "s/mcp/fs-cursor"])
    assert variant.exit_code == 0, variant.output
    vtext = " ".join(variant.output.split())
    assert "cursor ← fs-cursor.json (variant; replaces fs.json)" in vtext
    (row,) = [r for r in status(proj)[0] if r.ref == "s/mcp/fs-cursor"]
    assert row.target == "cursor" and row.replaces == "s/mcp/fs"


def test_variants_keep_sync_idempotent(tmp_path: Path):
    proj, _ = _mcp_pair(tmp_path, "fs", "fs-cursor")
    sync(proj)
    before = {f: (proj / f).read_bytes() for f in (".mcp.json", ".cursor/mcp.json")}
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert {f: (proj / f).read_bytes() for f in before} == before
