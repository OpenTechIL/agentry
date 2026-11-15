"""PM ruling (R3 runtime): directory tools are SKIPPED on OpenCode, with a warning.

OpenCode 1.19.5 loads only single `.ts`/`.js` files directly in `.opencode/tools/`; a copied
`tools/<name>/` directory is silently ignored (runtime-checked: `index.ts` and `<name>.ts`
inside the folder both stay unloaded). So agentry skips it there and says how to fix it. An
`expect` override lets it through; claude is unaffected; a directory agentry installed before
is removed on the next sync.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync

_C = ComponentType
_TS = b'export default { description: "t", args: {}, async execute() { return "ok" } }\n'
_LET_THROUGH = {  # a user override: the opencode tool rule without its built-in expectation
    "opencode": {"tool": {"strategy": "copy", "dest": ".opencode/tools/{name}{ext}", "expect": {}}}
}


def _line(ref: str = "s/tool/dirtool", name: str = "dirtool", source: str = "s") -> str:
    return (
        f"{ref}: OpenCode only loads single-file tools (.ts/.js directly in .opencode/tools/); "
        "this tool is a directory, so it was skipped on opencode. "
        f"Ship it as one file, tools/{name}.ts or .js, and add it with "
        f"`agentry add {source}/tool/{name} --path tools/{name}.ts`, "
        "or list it in the source's agentry.yaml."
    )


def _project(tmp_path: Path, targets=("opencode",), *, flat=False, profiles=None) -> Path:
    src = tmp_path / "src"
    (src / "tools" / "dirtool").mkdir(parents=True)
    (src / "tools" / "dirtool" / "index.ts").write_bytes(_TS)
    (src / "tools" / "flat.ts").write_bytes(_TS)
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, list(targets))
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.TOOL, name="dirtool"))
    if flat:
        store.add_component(Component(source="s", type=_C.TOOL, name="flat", path="tools/flat.ts"))
    if profiles:
        store.doc["target_profiles"] = profiles
    store.save()
    return proj


def _set_profiles(proj: Path, profiles: dict | None) -> None:
    store = ConfigStore.load(proj)
    if profiles is None:
        store.doc.pop("target_profiles", None)
    else:
        store.doc["target_profiles"] = profiles
    store.save()


def _oc_tools(proj: Path) -> list[str]:
    d = proj / ".opencode" / "tools"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


# --- skipped -------------------------------------------------------------------------------


def test_directory_tool_skipped_on_opencode_with_exact_warning(tmp_path: Path):
    proj = _project(tmp_path)
    res = sync(proj)
    assert res.warnings.count(_line()) == 1
    assert _oc_tools(proj) == []  # nothing written under .opencode/tools/


def test_directory_tool_status_missing_and_doctor_warns(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path)
    sync(proj)
    (row,) = [r for r in status(proj)[0] if r.ref == "s/tool/dirtool" and r.target == "opencode"]
    assert row.state == "missing"
    assert row.where == ".opencode/tools/dirtool"
    assert (
        row.note
        == "skipped on opencode: 'dirtool' is a directory, not a single file (see warnings)"
    )
    checks = [(c.level, c.category) for c in run_checks(proj) if c.message == _line()]
    assert checks == [("warn", "format")]
    monkeypatch.chdir(proj)
    env = {"COLUMNS": "400", "PATH": "/usr/bin:/bin"}  # no `agy` alias warning in the way
    assert CliRunner(env=env).invoke(app, ["doctor", "--strict"]).exit_code == 1


def test_flat_tool_still_installs(tmp_path: Path):
    """Guard: a single-file tool installs as `<name>.ts`, a copy (D1+D2)."""
    proj = _project(tmp_path, flat=True)
    res = sync(proj)
    flat = proj / ".opencode/tools/flat.ts"
    assert flat.is_file() and not flat.is_symlink() and flat.read_bytes() == _TS
    assert not [w for w in res.warnings if w.startswith("s/tool/flat:")]


def test_flat_installs_while_directory_is_skipped(tmp_path: Path):
    proj = _project(tmp_path, flat=True)
    res = sync(proj)
    assert _oc_tools(proj) == ["flat.ts"]
    assert res.warnings.count(_line()) == 1


def test_expect_override_lets_the_directory_tool_through(tmp_path: Path):
    """Guard (holds today): with `expect: {}` the user opts out of the check and owns it."""
    proj = _project(tmp_path, profiles=_LET_THROUGH)
    res = sync(proj)
    assert (proj / ".opencode/tools/dirtool/index.ts").read_bytes() == _TS
    assert _line() not in res.warnings


def test_claude_directory_tool_still_installs(tmp_path: Path):
    """Guard: claude's rule is untouched — the directory links as `.claude/tools/dirtool`."""
    proj = _project(tmp_path, targets=("claude",))
    res = sync(proj)
    assert (proj / ".claude/tools/dirtool/index.ts").read_bytes() == _TS
    assert _line() not in res.warnings


def test_mixed_targets_claude_installs_opencode_skips(tmp_path: Path):
    proj = _project(tmp_path, targets=("claude", "opencode"))
    res = sync(proj)
    assert (proj / ".claude/tools/dirtool/index.ts").is_file()
    assert _oc_tools(proj) == []
    assert res.warnings.count(_line()) == 1


# --- upgrade -------------------------------------------------------------------------------


def test_previously_installed_directory_removed_user_file_untouched(tmp_path: Path):
    """Installed by an older agentry (simulated with the let-through override): once the skip
    applies, the next sync removes agentry's directory copy; the user's own file in
    `.opencode/tools/` is left alone."""
    proj = _project(tmp_path, profiles=_LET_THROUGH)
    sync(proj)
    assert (proj / ".opencode/tools/dirtool/index.ts").is_file()
    (proj / ".opencode/tools/mine.ts").write_bytes(b"// mine\n")
    _set_profiles(proj, None)

    res = sync(proj)
    assert not (proj / ".opencode/tools/dirtool").exists()
    assert (proj / ".opencode/tools/mine.ts").read_bytes() == b"// mine\n"
    assert _oc_tools(proj) == ["mine.ts"]
    assert res.warnings.count(_line()) == 1


# --- stability -----------------------------------------------------------------------------


def test_directory_tool_skip_is_idempotent(tmp_path: Path):
    proj = _project(tmp_path, targets=("claude", "opencode"), flat=True)
    first = sync(proj)
    second = sync(proj)
    assert second.created == [] and second.updated == [] and second.removed == []
    assert sorted(second.warnings) == sorted(first.warnings)
    assert _oc_tools(proj) == ["flat.ts"]


def test_single_non_ts_file_is_not_skipped(tmp_path: Path):
    """Scope: the rule is about directories only — a single file of another kind installs."""
    src = tmp_path / "src"
    (src / "tools").mkdir(parents=True)
    (src / "tools" / "helper.py").write_bytes(b"print('hi')\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.TOOL, name="helper", path="tools/helper.py"))
    store.save()
    res = sync(proj)
    assert (proj / ".opencode/tools/helper.py").read_bytes() == b"print('hi')\n"
    assert not [w for w in res.warnings if "skipped on opencode" in w]


def test_file_only_false_override_lets_the_directory_through(tmp_path: Path):
    profiles = {
        "opencode": {
            "tool": {
                "strategy": "copy",
                "dest": ".opencode/tools/{name}{ext}",
                "expect": {"file_only": False},
            }
        }
    }
    proj = _project(tmp_path, profiles=profiles)
    res = sync(proj)
    assert (proj / ".opencode/tools/dirtool/index.ts").read_bytes() == _TS
    assert _line() not in res.warnings


def test_previously_linked_directory_tool_removed_on_upgrade(tmp_path: Path):
    """Pre-D2 installs linked the directory (`.opencode/tools/dirtool` -> store). Once the skip
    applies, the next sync removes agentry's link; the user's own file stays."""
    legacy = {
        "opencode": {"tool": {"strategy": "link", "dest": ".opencode/tools/{name}", "expect": {}}}
    }
    proj = _project(tmp_path, profiles=legacy)
    sync(proj)
    link = proj / ".opencode/tools/dirtool"
    assert link.is_symlink()
    (proj / ".opencode/tools/mine.ts").write_bytes(b"// mine\n")
    _set_profiles(proj, None)

    res = sync(proj)
    assert not link.exists() and not link.is_symlink()
    assert _oc_tools(proj) == ["mine.ts"]
    assert (tmp_path / "src/tools/dirtool/index.ts").read_bytes() == _TS  # source untouched
    assert res.warnings.count(_line()) == 1


@pytest.mark.parametrize("subdir", [None, "sub"], ids=["no_subdir", "subdir"])
def test_following_the_printed_command_verbatim_installs_the_tool(
    tmp_path: Path, monkeypatch, subdir
):
    """The hint is runnable: create `tools/dirtool.ts` where the warning says, run the printed
    `agentry add …` exactly as shown, and the tool installs as `.opencode/tools/dirtool.ts`
    with the warning gone. `--path` is relative to the source's effective root, so it's the
    same command with or without a source subdir."""
    import re
    import shlex

    from agentry.cli import app

    src = tmp_path / "src"
    root = src / subdir if subdir else src
    (root / "tools" / "dirtool").mkdir(parents=True)
    (root / "tools" / "dirtool" / "index.ts").write_bytes(_TS)
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src), subdir=subdir))
    store.add_component(Component(source="s", type=_C.TOOL, name="dirtool"))
    store.save()

    (warning,) = [w for w in sync(proj).warnings if "skipped on opencode" in w]
    assert warning == _line()
    (cmd,) = re.findall(r"`(agentry add [^`]+)`", warning)

    (root / "tools" / "dirtool.ts").write_bytes(_TS)  # "Ship it as one file, tools/dirtool.ts"
    monkeypatch.chdir(proj)
    res = CliRunner(env={"COLUMNS": "200"}).invoke(app, shlex.split(cmd)[1:])
    assert res.exit_code == 0, res.output
    after = sync(proj)
    dest = proj / ".opencode/tools/dirtool.ts"
    assert dest.is_file() and not dest.is_symlink() and dest.read_bytes() == _TS
    assert not [w for w in after.warnings if "skipped on opencode" in w]
