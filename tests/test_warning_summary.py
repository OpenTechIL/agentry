"""T14: repeated per-sync warnings collapse to one summary line on the CLI.

`SyncResult.warnings` stays complete; only the CLI rendering summarizes, and only for the
repetitive kinds (unsupported-type skips, W2 kept-as-is drift) when there are >= 2 of them.
`--verbose` restores the full list. Everything else (W1/W3/W5, …) always prints in full.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import sync

_C = ComponentType
runner = CliRunner()

SKIP_SUMMARY = (
    "  ! 2 components skipped on targets that don't support their type — run with "
    "`--verbose` for the list (expected; see `agentry target drivers`)"
)
DRIFT_SUMMARY = (
    "  ! 2 merged entries differ from their source and were kept as-is — run "
    "`agentry doctor` for the list"
)


def _source(root: Path, *, skills: tuple[str, ...], mcp_keys: tuple[str, ...]) -> Path:
    for name in skills:
        (root / "skills" / name).mkdir(parents=True)
        (root / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    if mcp_keys:
        (root / "mcp").mkdir(parents=True)
        (root / "mcp" / "servers.json").write_text(
            json.dumps({k: {"command": k} for k in mcp_keys}), encoding="utf-8"
        )
    return root


def _project(
    tmp_path: Path,
    *,
    skills: tuple[str, ...] = (),
    mcp_keys: tuple[str, ...] = (),
    targets: tuple[Target, ...] = (Target.CLAUDE, Target.CURSOR),
) -> Path:
    src = _source(tmp_path / "src", skills=skills, mcp_keys=mcp_keys)
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, list(targets)).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for name in skills:
        store.add_component(Component(source="s", type=_C.SKILL, name=name))
    if mcp_keys:
        store.add_component(Component(source="s", type=_C.MCP, name="servers"))
    store.save()
    return proj


def _edit_all_mcp(proj: Path) -> None:
    cfg = proj / ".mcp.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    for key in doc["mcpServers"]:
        doc["mcpServers"][key] = {"command": f"{key}-edited"}
    cfg.write_text(json.dumps(doc), encoding="utf-8")


def _warn_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if ln.startswith("  ! ")]


def _cli_sync(proj: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> list[str]:
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["sync", *args])
    assert result.exit_code == 0, result.output
    return _warn_lines(result.stderr)


def _skip_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "does not support" in w and w.endswith("skipped")]


def _drift_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "differs" in w or "edited after agentry installed it" in w]


# --- (a) N = 1 prints in full, N >= 2 collapses -----------------------------------------


def test_single_unsupported_skip_prints_in_full(tmp_path, monkeypatch):
    proj = _project(tmp_path, skills=("a",))
    full = _skip_warnings(sync(proj).warnings)
    assert len(full) == 1

    lines = _cli_sync(proj, monkeypatch)
    assert f"  ! {full[0]}" in lines
    assert SKIP_SUMMARY not in lines


def test_repeated_unsupported_skips_collapse_to_one_summary(tmp_path, monkeypatch):
    proj = _project(tmp_path, skills=("a", "b"))
    full = _skip_warnings(sync(proj).warnings)
    assert len(full) == 2

    lines = _cli_sync(proj, monkeypatch)
    assert lines.count(SKIP_SUMMARY) == 1
    assert not any(f"  ! {w}" in lines for w in full)


def test_single_drift_prints_in_full(tmp_path, monkeypatch):
    proj = _project(tmp_path, mcp_keys=("github",), targets=(Target.CLAUDE,))
    sync(proj)
    _edit_all_mcp(proj)
    full = _drift_warnings(sync(proj).warnings)
    assert len(full) == 1

    lines = _cli_sync(proj, monkeypatch)
    assert f"  ! {full[0]}" in lines
    assert DRIFT_SUMMARY not in lines


def test_repeated_drift_collapses_to_one_summary(tmp_path, monkeypatch):
    proj = _project(tmp_path, mcp_keys=("github", "gitlab"), targets=(Target.CLAUDE,))
    sync(proj)
    _edit_all_mcp(proj)
    full = _drift_warnings(sync(proj).warnings)
    assert len(full) == 2

    lines = _cli_sync(proj, monkeypatch)
    assert lines.count(DRIFT_SUMMARY) == 1
    assert not any(f"  ! {w}" in lines for w in full)


# --- (b) --verbose prints the full list --------------------------------------------------


@pytest.mark.parametrize("flag", ["--verbose", "-v"])
def test_verbose_prints_every_warning_in_full(tmp_path, monkeypatch, flag):
    proj = _project(tmp_path, skills=("a", "b"), mcp_keys=("github", "gitlab"))
    sync(proj)
    _edit_all_mcp(proj)
    expected = [f"  ! {w}" for w in sync(proj).warnings]
    assert len(expected) >= 4

    lines = _cli_sync(proj, monkeypatch, flag)
    assert lines == expected
    assert SKIP_SUMMARY not in lines and DRIFT_SUMMARY not in lines


# --- (c) SyncResult stays complete; `summarized` only indexes collapsible warnings --------


def test_sync_result_warnings_complete_and_summarized_is_a_subset(tmp_path):
    proj = _project(tmp_path, skills=("a", "b"), mcp_keys=("github", "gitlab"))
    sync(proj)
    _edit_all_mcp(proj)
    res = sync(proj)

    assert sorted(res.summarized["unsupported"]) == sorted(_skip_warnings(res.warnings))
    assert sorted(res.summarized.get("drift", [])) == sorted(_drift_warnings(res.warnings))
    for msgs in res.summarized.values():
        assert all(m in res.warnings for m in msgs)


def test_summarized_has_empty_unsupported_and_no_drift_without_merges(tmp_path):
    proj = _project(tmp_path, skills=("a",), targets=(Target.CLAUDE,))
    res = sync(proj)
    assert res.summarized["unsupported"] == []
    assert res.summarized.get("drift", []) == []


# --- (d) non-repetitive kinds always print individually ---------------------------------


def test_w1_refusals_print_individually_without_verbose(tmp_path, monkeypatch):
    """Two W1 refusals are actionable, so each prints in full; nothing is summarized."""
    proj = _project(tmp_path, mcp_keys=("github", "gitlab"), targets=(Target.CLAUDE,))
    (proj / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "MINE"}, "gitlab": {"command": "MINE"}}}),
        encoding="utf-8",
    )
    res = sync(proj)
    w1 = [w for w in res.warnings if "not added by agentry" in w]
    assert len(w1) == 2
    assert not any(w in msgs for msgs in res.summarized.values() for w in w1)

    lines = _cli_sync(proj, monkeypatch)
    for w in w1:
        assert f"  ! {w}" in lines
    assert DRIFT_SUMMARY not in lines


def test_w5_duplicate_claims_print_individually_without_verbose(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    for name in ("a", "b", "c"):
        (src / "mcp" / f"{name}.json").write_text(
            json.dumps({"dup1": {"command": name}, "dup2": {"command": name}}), encoding="utf-8"
        )
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for name in ("a", "b", "c"):
        store.add_component(Component(source="s", type=_C.MCP, name=name))
    store.save()

    res = sync(proj)
    w5 = [w for w in res.warnings if "is already provided by" in w]
    assert len(w5) >= 2
    assert not any(w in msgs for msgs in res.summarized.values() for w in w5)

    lines = _cli_sync(proj, monkeypatch)
    for w in w5:
        assert f"  ! {w}" in lines


def test_w3_unparseable_target_prints_individually_without_verbose(tmp_path, monkeypatch):
    proj = _project(tmp_path, mcp_keys=("github", "gitlab"), targets=(Target.CLAUDE,))
    (proj / ".mcp.json").write_text('{"mcpServers": {', encoding="utf-8")
    res = sync(proj)
    w3 = [w for w in res.warnings if "not merged into its config" in w]
    assert w3
    assert not any(w in msgs for msgs in res.summarized.values() for w in w3)

    lines = _cli_sync(proj, monkeypatch)
    for w in w3:
        assert f"  ! {w}" in lines


# --- the flag and the summary apply to every installing command -------------------------


@pytest.mark.parametrize("cmd", ["install", "update"])
def test_install_and_update_summarize_and_accept_verbose(tmp_path, monkeypatch, cmd):
    proj = _project(tmp_path, skills=("a", "b"))
    full = [f"  ! {w}" for w in _skip_warnings(sync(proj).warnings)]
    monkeypatch.chdir(proj)

    quiet = runner.invoke(app, [cmd])
    assert quiet.exit_code == 0, quiet.output
    assert _warn_lines(quiet.stderr).count(SKIP_SUMMARY) == 1

    loud = runner.invoke(app, [cmd, "-v"])
    assert loud.exit_code == 0, loud.output
    assert all(line in _warn_lines(loud.stderr) for line in full)
    assert SKIP_SUMMARY not in _warn_lines(loud.stderr)


def test_add_prints_single_skip_in_full_then_summary_on_second(tmp_path, monkeypatch):
    src = _source(tmp_path / "src", skills=("a", "b"), mcp_keys=())
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE, Target.CURSOR]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.save()
    monkeypatch.chdir(proj)

    first = runner.invoke(app, ["add", "s/skill/a"])
    assert first.exit_code == 0, first.output
    lines = _warn_lines(first.stderr)
    assert any("s/skill/a" in ln and "does not support" in ln for ln in lines)
    assert SKIP_SUMMARY not in lines

    second = runner.invoke(app, ["add", "s/skill/b"])
    assert second.exit_code == 0, second.output
    assert _warn_lines(second.stderr).count(SKIP_SUMMARY) == 1

    loud = runner.invoke(app, ["add", "s/skill/b", "--verbose"])
    assert loud.exit_code == 0, loud.output
    assert SKIP_SUMMARY not in _warn_lines(loud.stderr)
