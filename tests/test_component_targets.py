"""T18: a component's `targets:` intersects the project's `targets:` (docs win).

A component may only install into targets the project has activated; a defined-but-
inactive one is skipped with a warning (never written), and an install that landed outside
the project's targets before this rule is removed on the next sync. An *undefined* name is
still fatal (T17).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.models import Component, ComponentType, GeneratorSpec, Source, SourceType, Target
from agentry.reconcile import sync
from agentry.targets import UnresolvedTargetError

_C = ComponentType
runner = CliRunner()


def _skip_warning(ref: str, target: str) -> str:
    return (
        f"{ref}: target '{target}' is not in the project's targets — skipped. Add it with "
        f"`agentry target use {target}`, or remove it from this component."
    )


def _source(root: Path) -> Path:
    (root / "skills" / "rev").mkdir(parents=True)
    (root / "skills" / "rev" / "SKILL.md").write_text(
        "---\nname: rev\ndescription: Use when reviewing code.\n---\nbody\n", encoding="utf-8"
    )
    (root / "mcp").mkdir()
    (root / "mcp" / "gh.json").write_text(json.dumps({"gh": {"command": "npx"}}), encoding="utf-8")
    return root


def _project(tmp_path: Path, *comps: Component, targets=(Target.CLAUDE,)) -> Path:
    src = _source(tmp_path / "src")
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, list(targets)).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for c in comps:
        store.add_component(c)
    store.save()
    return proj


def _gh(targets: list[str]) -> Component:
    return Component(source="s", type=_C.MCP, name="gh", targets=targets)


def _rev(targets: list[str] | None = None) -> Component:
    return Component(source="s", type=_C.SKILL, name="rev", targets=targets)


def _warn_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if ln.startswith("  ! ")]


# --- inactive component targets are skipped, never written ------------------------------


def test_inactive_component_target_is_skipped_with_warning(tmp_path: Path):
    proj = _project(tmp_path, _gh(["claude", "copilot"]))
    res = sync(proj)

    assert _skip_warning("s/mcp/gh", "copilot") in res.warnings
    assert "gh" in json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert not (proj / ".vscode").exists()  # nothing written for the inactive target


def test_component_with_no_targets_gets_the_full_project_list(tmp_path: Path):
    proj = _project(tmp_path, _rev(), targets=(Target.CLAUDE, Target.GEMINI))
    res = sync(proj)
    assert (proj / ".claude/skills/rev").is_symlink()
    assert (proj / ".gemini/skills/rev").is_symlink()
    assert not any("is not in the project's targets" in w for w in res.warnings)


def test_component_whose_only_target_is_inactive_installs_nothing(tmp_path: Path):
    proj = _project(tmp_path, _gh(["copilot"]))
    res = sync(proj)
    assert _skip_warning("s/mcp/gh", "copilot") in res.warnings
    assert not (proj / ".mcp.json").exists() and not (proj / ".vscode").exists()


def test_typo_in_component_targets_still_fatal(tmp_path: Path):
    proj = _project(tmp_path, _gh(["claude", "copiolt"]))
    with pytest.raises(UnresolvedTargetError, match="did you mean 'copilot'"):
        sync(proj)


# --- migration: an over-scoped install is removed --------------------------------------


def test_over_scoped_install_removed_on_next_sync(tmp_path: Path):
    """Before T18 a component `targets: [claude, copilot]` installed into copilot even in a
    claude-only project. Reproduced by installing with copilot active, then dropping it from
    the project: the next sync removes our copilot key and leaves the user's entries."""
    proj = _project(tmp_path, _gh(["claude", "copilot"]), targets=(Target.CLAUDE, Target.COPILOT))
    sync(proj)
    vs = proj / ".vscode/mcp.json"
    doc = json.loads(vs.read_text(encoding="utf-8"))
    assert "gh" in doc["servers"]
    doc["servers"]["mine"] = {"command": "MINE"}
    vs.write_text(json.dumps(doc), encoding="utf-8")

    store = ConfigStore.load(proj)
    store.doc["targets"] = ["claude"]
    store.save()
    res = sync(proj)

    assert any(".vscode/mcp.json" in r and "gh" in r for r in res.removed)
    assert json.loads(vs.read_text(encoding="utf-8"))["servers"] == {"mine": {"command": "MINE"}}
    assert _skip_warning("s/mcp/gh", "copilot") in res.warnings


# --- summary, doctor, idempotency -------------------------------------------------------


def test_single_inactive_target_prints_in_full(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, _gh(["claude", "copilot"]))
    monkeypatch.chdir(proj)
    lines = _warn_lines(runner.invoke(app, ["sync"]).stderr)
    assert f"  ! {_skip_warning('s/mcp/gh', 'copilot')}" in lines


def test_repeated_inactive_targets_summarize(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, _gh(["claude", "copilot"]), _rev(["claude", "gemini"]))
    full = [w for w in sync(proj).warnings if "is not in the project's targets" in w]
    assert len(full) == 2

    monkeypatch.chdir(proj)
    lines = _warn_lines(runner.invoke(app, ["sync"]).stderr)
    summary = (
        "  ! 2 component targets aren't active in this project and were skipped — run "
        "`agentry doctor` for the list"
    )
    assert lines.count(summary) == 1
    assert not any(f"  ! {w}" in lines for w in full)
    verbose = _warn_lines(runner.invoke(app, ["sync", "--verbose"]).stderr)
    assert all(f"  ! {w}" in verbose for w in full) and summary not in verbose


def test_doctor_warns_on_inactive_component_target_and_strict_fails(tmp_path, monkeypatch):
    proj = _project(tmp_path, _gh(["claude", "copilot"]))
    sync(proj)
    checks = [c for c in run_checks(proj) if c.category == "target" and c.level == "warn"]
    assert [c.message for c in checks] == [_skip_warning("s/mcp/gh", "copilot")]
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["doctor", "--strict"]).exit_code == 1


def test_inactive_component_targets_keep_sync_idempotent(tmp_path: Path):
    proj = _project(tmp_path, _gh(["claude", "copilot"]), _rev(["claude", "gemini"]))
    sync(proj)
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert not (proj / ".vscode").exists() and not (proj / ".gemini").exists()


# --- other surfaces ---------------------------------------------------------------------


def test_emit_triggers_ignores_inactive_component_targets(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, _rev(["claude", "gemini"]))
    sync(proj)
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["emit", "triggers"])
    assert result.exit_code == 0, result.output
    assert "Use when reviewing code." in (proj / ".claude/CLAUDE.md").read_text(encoding="utf-8")
    assert not (proj / "GEMINI.md").exists()  # gemini isn't a project target
    assert "GEMINI.md" not in result.output


def test_target_list_shows_referenced_not_active(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, _gh(["claude", "copilot"]), _rev(["copilot"]))
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["target", "list"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "Referenced but not active (components skip these)" in text
    assert "copilot ← s/mcp/gh, s/skill/rev — activate with `agentry target use copilot`" in text


def test_generate_skipped_when_no_target_overlap(tmp_path: Path):
    marker = ".claude/skills/fake/SKILL.md"
    script = (
        "import os;"
        f"os.makedirs(os.path.dirname({marker!r}), exist_ok=True);"
        f"open({marker!r}, 'w').write('x')"
    )
    spec = GeneratorSpec(command=[sys.executable, "-c", script], produces=[os.path.dirname(marker)])
    comp = Component(source="s", type=_C.SKILL, name="fake", generate=spec, targets=["copilot"])
    proj = _project(tmp_path, comp)

    res = sync(proj, allow_run=True)  # gated on overlap, not on --allow-run
    assert _skip_warning("s/skill/fake", "copilot") in res.warnings
    assert not (proj / marker).exists()  # generator never ran


def test_keep_on_inactive_target_exits_1(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, _gh(["claude", "copilot"]))
    sync(proj)
    cfg_before = (proj / ".agentry.yml").read_bytes()
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["keep", "s/mcp/gh", "copilot:gh"])
    assert result.exit_code == 1
    assert (
        "target 'copilot' isn't in the project's targets — add it with `agentry target use "
        "copilot`, or keep the bare '<key>' for the targets s/mcp/gh does install into."
    ) in " ".join((result.output + result.stderr).split())
    assert (proj / ".agentry.yml").read_bytes() == cfg_before


def test_keep_on_unknown_target_still_says_unknown(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, _gh(["claude", "copilot"]))
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["keep", "s/mcp/gh", "nope:gh"])
    assert result.exit_code == 1
    assert "Unknown target 'nope' for s/mcp/gh" in " ".join((result.output + result.stderr).split())


@pytest.mark.parametrize(
    "n, expected",
    [
        (
            1,
            "1 component target isn't active in this project and was skipped — run "
            "`agentry doctor` for the list",
        ),
        (
            3,
            "3 component targets aren't active in this project and were skipped — run "
            "`agentry doctor` for the list",
        ),
    ],
)
def test_inactive_summary_wording_singular_and_plural(n, expected):
    """The singular is unreachable from the CLI (summaries need N >= 2), so pin the helper."""
    from agentry.cli import _summary_line

    assert _summary_line("inactive", n) == expected
