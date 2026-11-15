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


def _line(
    ref: str = "s/tool/dirtool",
    name: str = "dirtool",
    source: str = "s",
    why: str = "'dirtool' is a directory, not a single file",
    src_dir: str = "tools/",
) -> str:
    return (
        f"{ref}: OpenCode only loads single-file tools (.ts/.js directly in .opencode/tools/); "
        "this tool isn't one, so it was skipped on opencode. "
        f"Ship it as one file, {src_dir}{name}.ts or .js, and add it with "
        f"`agentry add {source}/tool/{name} --path {src_dir}{name}.ts`, "
        f"or list it in the source's agentry.yaml. ({why})"
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


@pytest.mark.parametrize("fname", ["helper.py", "helper"], ids=["py", "no_extension"])
def test_single_file_of_another_kind_is_skipped(tmp_path: Path, fname: str):
    """OpenCode loads only `.ts` / `.js`: a single `.py` or extension-less file would be
    ignored there too, so it's skipped with the same warning naming the problem."""
    src = tmp_path / "src"
    (src / "tools").mkdir(parents=True)
    (src / "tools" / fname).write_bytes(b"print('hi')\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude", "opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.TOOL, name="helper", path=f"tools/{fname}"))
    store.save()
    res = sync(proj)
    assert _oc_tools(proj) == []
    why = f"'{fname}' is not a .ts or .js file"
    assert res.warnings.count(_line("s/tool/helper", "helper", why=why)) == 1
    assert (proj / ".claude/tools/helper").exists()  # other targets unaffected
    assert sync(proj).created == []  # and the skip is stable


@pytest.mark.parametrize(
    ("rel", "src_dir"),
    [("scripts/fmt.py", "scripts/"), ("lib/tools/fmt.py", "lib/tools/"), ("fmt.py", "")],
    ids=["other_folder", "nested", "source_root"],
)
def test_hint_points_next_to_an_explicit_path(tmp_path: Path, monkeypatch, rel: str, src_dir: str):
    """A tool added with `--path` outside `tools/` gets a hint naming a `.ts` file next to
    the original — not `tools/<name>.ts` — and following it verbatim installs the tool."""
    import re
    import shlex

    src = tmp_path / "src"
    (src / rel).parent.mkdir(parents=True, exist_ok=True)
    (src / rel).write_bytes(b"print('hi')\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.TOOL, name="fmt", path=rel))
    store.save()

    (warning,) = [w for w in sync(proj).warnings if "skipped on opencode" in w]
    why = f"'{Path(rel).name}' is not a .ts or .js file"
    assert warning == _line("s/tool/fmt", "fmt", why=why, src_dir=src_dir)
    (cmd,) = re.findall(r"`(agentry add [^`]+)`", warning)

    (src / f"{src_dir}fmt.ts").write_bytes(_TS)
    monkeypatch.chdir(proj)
    res = CliRunner(env={"COLUMNS": "200"}).invoke(app, shlex.split(cmd)[1:])
    assert res.exit_code == 0, res.output
    after = sync(proj)
    assert (proj / ".opencode/tools/fmt.ts").read_bytes() == _TS
    assert not [w for w in after.warnings if "skipped on opencode" in w]


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


# --- review follow-ups -------------------------------------------------------------------


def _descriptor_project(tmp_path: Path, provides: str, *, keep_dir: bool = False) -> Path:
    """A source whose agentry.yaml lists a single-file tool; the project takes the whole
    source the way `agentry add s` would (the component named as discovery names it)."""
    from agentry import discovery

    src = tmp_path / "src"
    (src / "tools").mkdir(parents=True)
    (src / "tools" / "dirtool.ts").write_bytes(_TS)
    if keep_dir:
        (src / "tools" / "dirtool").mkdir()
        (src / "tools" / "dirtool" / "index.ts").write_bytes(_TS)
    (src / "agentry.yaml").write_text(f"version: 1\nprovides:\n  tool:\n{provides}", "utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for d in discovery.discover(src):
        store.add_component(Component(source="s", type=d.type, name=d.name))
    store.save()
    return proj


@pytest.mark.parametrize(
    "provides",
    ["    - { path: tools/dirtool.ts }\n", "    - { glob: 'tools/*.ts' }\n"],
    ids=["path", "glob"],
)
def test_descriptor_listed_single_file_tool_lands_once_with_its_extension(
    tmp_path: Path, provides: str
):
    """`path:` / `glob:` without `name:` names a tool after the whole file (`dirtool.ts`, since
    a tool is a dir type); it must land as `.opencode/tools/dirtool.ts`, not `dirtool.ts.ts`.
    The ref keeps the full name, so existing lock identities don't move."""
    proj = _descriptor_project(tmp_path, provides)
    res = sync(proj)
    assert _oc_tools(proj) == ["dirtool.ts"]
    assert (proj / ".opencode/tools/dirtool.ts").read_bytes() == _TS
    assert [c.ref for c in ConfigStore.load(proj).parsed().components] == ["s/tool/dirtool.ts"]
    assert not [w for w in res.warnings if "skipped on opencode" in w]
    assert sync(proj).created == []


def test_troubleshooting_fix_via_agentry_yaml_holds_end_to_end(tmp_path: Path):
    """docs/troubleshooting.md: list the single file in agentry.yaml with
    `path: tools/dirtool.ts` and agentry copies it to `.opencode/tools/dirtool.ts`."""
    proj = _descriptor_project(tmp_path, "    - { path: tools/dirtool.ts }\n", keep_dir=True)
    sync(proj)
    dest = proj / ".opencode/tools/dirtool.ts"
    assert dest.is_file() and not dest.is_symlink() and dest.read_bytes() == _TS
    assert _oc_tools(proj) == ["dirtool.ts"]


def test_directory_tool_with_a_transform_is_still_skipped(tmp_path: Path):
    """A transform only applies to single-file link types; on opencode (copy, and tool is a
    dir type) the raw directory would land, so the path check must still run."""
    from agentry.models import ComponentTransform

    proj = _project(tmp_path)
    store = ConfigStore.load(proj)
    store.doc["components"] = []
    store.add_component(
        Component(
            source="s",
            type=_C.TOOL,
            name="dirtool",
            transform=ComponentTransform(provider="strip-frontmatter"),
        )
    )
    store.save()
    res = sync(proj)
    assert _oc_tools(proj) == []
    assert res.warnings.count(_line()) == 1


def test_cli_folds_two_directory_tool_skips_with_a_real_reason(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    for name in ("one", "two"):
        (src / "tools" / name).mkdir(parents=True)
        (src / "tools" / name / "index.ts").write_bytes(_TS)
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for name in ("one", "two"):
        store.add_component(Component(source="s", type=_C.TOOL, name=name))
    store.save()
    monkeypatch.chdir(proj)
    env = {"COLUMNS": "400", "PATH": "/usr/bin:/bin"}
    out = CliRunner(env=env).invoke(app, ["sync"]).output
    for name in ("one", "two"):
        line = (
            f"s/tool/{name}: skipped on opencode — '{name}' is a directory, not a single file. "
            "Run `agentry doctor` (or sync with --verbose) for the fix"
        )
        assert out.count(line) == 1, out
    assert "—  use" not in out and "a format opencode rejects" not in out


def test_path_check_only_runs_where_a_skip_rule_exists(tmp_path: Path, monkeypatch):
    """Cheap early return: a link/copy pair with no `on_mismatch: skip` rule (every claude
    pair here) never reaches the skip check."""
    from agentry import reconcile

    calls: list[tuple[str, str]] = []
    real = reconcile._skip_on_mismatch

    def counting(comp, tname, *a, **kw):
        calls.append((comp.ref, tname))
        return real(comp, tname, *a, **kw)

    monkeypatch.setattr(reconcile, "_skip_on_mismatch", counting)
    proj = _project(tmp_path, targets=("claude", "opencode"), flat=True)
    sync(proj)
    assert calls and all(t == "opencode" for _, t in calls)
