"""T27: doctor resolves and plans once (reconcile.plan_for_status, status(planned=...)).

Golden guards pin today's exact `agentry doctor` / `agentry status` output on a fixture with
drift, a refused merge, an inactive component target and a format issue — the refactor must
not change a byte. The output is captured with a fixed width and no `agy` on PATH (doctor's
command check is environment-dependent), and the fixture has no absolute paths in it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry import deps
from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.models import Component, Source, SourceType
from agentry.models import ComponentType as C
from agentry.reconcile import plan_for_status, status, sync

_ENV = {"COLUMNS": "200", "PATH": "/usr/bin:/bin"}

DOCTOR_STDOUT = "  ! format  s/mcp/gh: may not work in opencode (expects `type: \"local\"` with `command` as one array and `environment`): 'gh' has no 'type'; 'gh'.command is a string, not an array; 'gh' uses 'args', \nwhich is not accepted — installed as-is. Ship an opencode-specific mcp, or override mcp for opencode under target_profiles\n  ! target  s/mcp/gh: target 'copilot' is not in the project's targets — skipped. Add it with `agentry target use copilot`, or remove it from this component.\n  ! drift  .mcp.json: 'mcpServers.gh' (from s/mcp/gh → claude) was edited by hand — delete it and run `agentry sync` to get the current version, or run `agentry keep s/mcp/gh claude:gh` to keep yours\n  ! drift  s/mcp/mine → claude: missing (.mcp.json already has 'mcpServers.mine' (not added by agentry) — rename or remove your entry, or disable s/mcp/mine)\ndoctor: 0 errors, 4 warning(s).\n"
DOCTOR_STDERR = ""
STATUS_STDOUT = "                      Install status                      \n┏━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓\n┃ ref        ┃ target   ┃ where                ┃ state   ┃\n┡━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩\n│ s/mcp/gh   │ claude   │ .mcp.json:mcpServers │ drift   │\n│ s/mcp/gh   │ opencode │ opencode.json:mcp    │ ok      │\n│ s/mcp/mine │ claude   │ .mcp.json:mcpServers │ missing │\n└────────────┴──────────┴──────────────────────┴─────────┘\n  s/mcp/gh → claude: edited by hand — delete the entry and run `agentry sync` to restore it, or run `agentry keep s/mcp/gh claude:gh` to keep yours\n  s/mcp/mine → claude: .mcp.json already has 'mcpServers.mine' (not added by agentry) — rename or remove your entry, or disable s/mcp/mine\n"
STATUS_STDERR = "! s/mcp/gh: target 'copilot' is not in the project's targets — skipped. Add it with `agentry target use copilot`, or remove it from this component.\n! s/mcp/gh: may not work in opencode (expects `type: \"local\"` with `command` as one array and `environment`): 'gh' has no 'type'; 'gh'.command is a string, not an array; 'gh' uses 'args', which is not\naccepted — installed as-is. Ship an opencode-specific mcp, or override mcp for opencode under target_profiles\n"


_BOX = re.compile("[\u2500-\u257f]")


def _portable(text: str) -> str:
    """Normalize what legitimately varies by platform: Rich's table box style (heavy vs light
    box-drawing glyphs on Windows) and where long lines wrap. Every word, every cell and every
    border position still has to match."""
    return " ".join(_BOX.sub("#", text).split())


def _golden_project(tmp: Path) -> Path:
    src = tmp / "src"
    (src / "mcp").mkdir(parents=True)
    (src / "mcp" / "gh.json").write_text(
        json.dumps({"gh": {"command": "npx", "args": ["-y", "gh"]}})
    )
    (src / "mcp" / "mine.json").write_text(json.dumps({"mine": {"command": "theirs"}}))
    proj = tmp / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude", "opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(
        Component(source="s", type=C.MCP, name="gh", targets=["claude", "opencode", "copilot"])
    )
    store.add_component(Component(source="s", type=C.MCP, name="mine", targets=["claude"]))
    store.save()
    # refused merge: the user already has a differing `mine` on claude
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "USER"}}}))
    sync(proj)
    # drift: hand-edit the owned gh entry
    doc = json.loads((proj / ".mcp.json").read_text())
    doc["mcpServers"]["gh"]["args"] = ["EDITED"]
    (proj / ".mcp.json").write_text(json.dumps(doc))
    return proj


@pytest.mark.parametrize("cmd", ["doctor", "status"])
def test_golden_doctor_and_status_output_unchanged(tmp_path: Path, monkeypatch, cmd: str):
    proj = _golden_project(tmp_path)
    monkeypatch.chdir(proj)
    res = CliRunner(env=_ENV).invoke(app, [cmd])
    assert res.exit_code == 0, res.output
    expected = {"doctor": (DOCTOR_STDOUT, DOCTOR_STDERR), "status": (STATUS_STDOUT, STATUS_STDERR)}
    want_out, want_err = expected[cmd]
    assert _portable(res.stdout) == _portable(want_out)
    assert _portable(res.stderr) == _portable(want_err)


def test_status_reusing_a_plan_matches_status_planning_itself(tmp_path: Path):
    """Same-run, byte-exact: status(planned=...) (doctor's path) equals status() planning on its
    own, rows and warnings both."""
    proj = _golden_project(tmp_path)
    config = ConfigStore.load(proj).parsed()
    assert status(proj, planned=plan_for_status(proj, config)) == status(proj)


def test_portable_normalization_still_discriminates():
    """Guard the guard: normalization absorbs box style and wrapping, nothing else."""
    assert _portable("┏━┳┓\n┃ a ┃") == _portable("┌─┬┐\n│ a │")
    assert _portable("which is not\naccepted") == _portable("which is \nnot accepted")
    assert _portable(STATUS_STDOUT) != _portable(STATUS_STDOUT.replace("drift", "ok", 1))
    assert _portable(STATUS_STDOUT) != _portable(STATUS_STDOUT.replace("│ ok", "  ok", 1))


def test_doctor_with_undefined_target_shows_target_error_and_no_drift(tmp_path: Path):
    """Guard: an unresolvable target stops planning, so doctor reports it and no drift rows."""
    proj = _golden_project(tmp_path)
    store = ConfigStore.load(proj)
    store.doc["targets"].append("clade")
    store.save()
    checks = run_checks(proj)
    assert any(
        c.level == "error" and c.message.startswith("target 'clade' is not defined") for c in checks
    )
    assert not [c for c in checks if c.category == "drift"]


def test_doctor_resolves_the_graph_once(tmp_path: Path, monkeypatch):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    repo = tmp_path / "repo"
    (repo / "mcp").mkdir(parents=True)
    (repo / "mcp" / "gh.json").write_text(json.dumps({"gh": {"command": "npx"}}), "utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env, capture_output=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="g", type=SourceType.GIT, url=f"file://{repo}", ref="main"))
    store.add_component(Component(source="g", type=C.MCP, name="gh"))
    store.save()
    sync(proj)

    calls = []
    real = deps.resolve_graph

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(deps, "resolve_graph", counting)
    run_checks(proj)
    assert len(calls) == 1
