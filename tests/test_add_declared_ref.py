"""PM ruling "`add <ref> --path` on an already-declared ref": update from explicit options and
say what really happened — never a silent no-op reported as "Added".

- options that differ: only those fields change ("Updated {ref}: path unset → new"), then sync;
- no options, or the same values: nothing changes ("{ref} is already declared — no change.");
- declared but disabled: never silently enabled; options still applied and reported;
- a new ref: "Added {ref}" as before. Exit 0 throughout; a repeat writes nothing.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore

_YML = """\
# team config — keep this comment
version: 1
targets:
  - claude
sources:
  # the shared source
  - name: s
    type: local
    path: {src}
components:
  # first: the command we will retarget
  - source: s
    type: command
    name: c
    targets: [claude]  # inline comment stays
  - source: s
    type: skill
    name: rev
"""


def _setup(tmp_path: Path, monkeypatch, *, disabled: bool = False) -> Path:
    src = tmp_path / "src"
    (src / "commands").mkdir(parents=True)
    (src / "commands" / "c.md").write_bytes(b"# canonical\n")
    (src / "alt").mkdir()
    (src / "alt" / "c.md").write_bytes(b"# alternative\n")
    (src / "skills" / "rev").mkdir(parents=True)
    (src / "skills" / "rev" / "SKILL.md").write_bytes(b"---\nname: rev\ndescription: d\n---\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    yml = _YML.format(src=src.as_posix())
    if disabled:
        yml = yml.replace(
            "    targets: [claude]  # inline comment stays\n",
            "    targets: [claude]  # inline comment stays\n    enabled: false\n",
        )
    (proj / ".agentry.yml").write_bytes(yml.encode("utf-8"))
    monkeypatch.chdir(proj)
    assert _cli("sync").exit_code == 0
    return proj


def _cli(*args: str):
    return CliRunner(env={"COLUMNS": "250"}).invoke(app, list(args))


def _text(res) -> str:
    return " ".join(res.output.split())


def _comp(proj: Path, name: str):
    (c,) = [c for c in ConfigStore.load(proj).parsed().components if c.name == name]
    return c


# --- options that differ: update -----------------------------------------------------------


def test_path_on_declared_ref_updates_only_path_and_syncs(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    assert (proj / ".claude/commands/c.md").read_bytes() == b"# canonical\n"
    res = _cli("add", "s/command/c", "--path", "alt/c.md")
    assert res.exit_code == 0, res.output
    assert "Updated s/command/c: path unset → alt/c.md" in _text(res)
    assert "Added" not in _text(res)
    c = _comp(proj, "c")
    assert c.path == "alt/c.md"
    assert c.targets == ["claude"] and c.enabled is not False  # unspecified fields untouched
    assert (proj / ".claude/commands/c.md").read_bytes() == b"# alternative\n"  # synced


def test_changing_an_existing_path_reports_old_and_new(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _cli("add", "s/command/c", "--path", "alt/c.md")
    res = _cli("add", "s/command/c", "--path", "commands/c.md")
    assert res.exit_code == 0, res.output
    assert "Updated s/command/c: path alt/c.md → commands/c.md" in _text(res)
    assert (proj / ".claude/commands/c.md").read_bytes() == b"# canonical\n"


def test_update_preserves_comments_and_order(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    before = (proj / ".agentry.yml").read_text("utf-8")
    _cli("add", "s/command/c", "--path", "alt/c.md")
    after = (proj / ".agentry.yml").read_text("utf-8")
    for comment in (
        "# team config — keep this comment",
        "# the shared source",
        "# first: the command we will retarget",
        "# inline comment stays",
    ):
        assert comment in after, comment
    names = [ln.strip() for ln in after.splitlines() if ln.strip().startswith("name:")]
    assert names == [ln.strip() for ln in before.splitlines() if ln.strip().startswith("name:")]
    # exactly one line added: the new `path:` key
    added = [ln for ln in after.splitlines() if ln not in before.splitlines()]
    assert [ln.strip() for ln in added] == ["path: alt/c.md"]


# --- nothing to change ---------------------------------------------------------------------


def test_same_value_again_is_no_change_and_byte_identical(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _cli("add", "s/command/c", "--path", "alt/c.md")
    cfg, lock = (proj / ".agentry.yml").read_bytes(), (proj / ".agentry.lock").read_bytes()
    res = _cli("add", "s/command/c", "--path", "alt/c.md")
    assert res.exit_code == 0, res.output
    assert "s/command/c is already declared — no change." in _text(res)
    assert "Updated" not in _text(res) and "Added" not in _text(res)
    assert (proj / ".agentry.yml").read_bytes() == cfg
    assert (proj / ".agentry.lock").read_bytes() == lock


def test_no_options_on_declared_ref_is_no_change(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    cfg = (proj / ".agentry.yml").read_bytes()
    res = _cli("add", "s/skill/rev")
    assert res.exit_code == 0, res.output
    assert "s/skill/rev is already declared — no change." in _text(res)
    assert "Added" not in _text(res)
    assert (proj / ".agentry.yml").read_bytes() == cfg


# --- disabled ------------------------------------------------------------------------------


def test_disabled_ref_is_not_enabled_and_gets_the_hint(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch, disabled=True)
    cfg = (proj / ".agentry.yml").read_bytes()
    res = _cli("add", "s/command/c")
    assert res.exit_code == 0, res.output
    assert (
        "s/command/c is already declared but disabled — run `agentry enable s/command/c`."
        in _text(res)
    )
    assert _comp(proj, "c").enabled is False
    assert (proj / ".agentry.yml").read_bytes() == cfg
    assert not (proj / ".claude/commands/c.md").exists()


def test_disabled_ref_with_options_applies_and_reports_but_stays_disabled(
    tmp_path: Path, monkeypatch
):
    proj = _setup(tmp_path, monkeypatch, disabled=True)
    res = _cli("add", "s/command/c", "--path", "alt/c.md")
    assert res.exit_code == 0, res.output
    out = _text(res)
    assert "Updated s/command/c: path unset → alt/c.md" in out
    assert "s/command/c is already declared but disabled — run `agentry enable s/command/c`." in out
    c = _comp(proj, "c")
    assert c.path == "alt/c.md" and c.enabled is False
    assert not (proj / ".claude/commands/c.md").exists()  # still not installed


# --- a new ref is unchanged ----------------------------------------------------------------


def test_new_ref_still_says_added(tmp_path: Path, monkeypatch):
    """Guard: the first `add` of a ref behaves exactly as before."""
    proj = _setup(tmp_path, monkeypatch)
    (tmp_path / "src/commands/d.md").write_bytes(b"# d\n")
    res = _cli("add", "s/command/d")
    assert res.exit_code == 0, res.output
    assert "Added s/command/d" in _text(res)
    assert (proj / ".claude/commands/d.md").read_bytes() == b"# d\n"


def test_new_ref_with_path_still_says_added(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    res = _cli("add", "s/command/e", "--path", "alt/c.md")
    assert res.exit_code == 0, res.output
    assert "Added s/command/e" in _text(res)
    assert _comp(proj, "e").path == "alt/c.md"


# --- stability -----------------------------------------------------------------------------


def test_add_update_is_idempotent(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _cli("add", "s/command/c", "--path", "alt/c.md")
    snap = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file() and ".agentry/" not in str(p)}
    for _ in range(2):
        res = _cli("add", "s/command/c", "--path", "alt/c.md")
        assert res.exit_code == 0 and "no change" in _text(res)
    assert {p: p.read_bytes() for p in snap} == snap


def test_path_vs_generate_conflict_exits_1_writes_nothing(tmp_path: Path, monkeypatch):
    """A component can't have both `path` and `generate`: in either direction the add is
    refused with exit 1, "Nothing was changed.", no sync, and byte-identical config/lock."""
    proj = _setup(tmp_path, monkeypatch)
    _cli("add", "s/command/c", "--path", "alt/c.md")
    gen = ["--generate-command", "echo hi", "--produces", ".claude/skills/gen"]
    assert _cli("add", "s/skill/gen", *gen).exit_code == 0
    assert _comp(proj, "gen").generate is not None

    for argv in (
        ["add", "s/command/c", *gen],  # path: declared, --generate-command given
        ["add", "s/skill/gen", "--path", "skills/rev"],  # generate: declared, --path given
    ):
        cfg, lock = (proj / ".agentry.yml").read_bytes(), (proj / ".agentry.lock").read_bytes()
        res = _cli(*argv)
        assert res.exit_code == 1, (argv, res.output)
        err = " ".join(res.stderr.split())
        assert "a component can't have both `path` and `generate`" in err
        assert err.endswith("Nothing was changed.")
        assert "resolved" not in res.output and "Updated" not in res.output  # no sync
        assert (proj / ".agentry.yml").read_bytes() == cfg
        assert (proj / ".agentry.lock").read_bytes() == lock
    assert _comp(proj, "c").path == "alt/c.md" and _comp(proj, "c").generate is None
    assert _comp(proj, "gen").path is None


def test_generate_on_declared_ref_updates_and_reports_command(tmp_path: Path, monkeypatch):
    """`--generate-command` on a declared ref without `path:`: set, then changed (old command
    shown), then repeated (no change, byte-identical). Setup commands round-trip too."""
    proj = _setup(tmp_path, monkeypatch)
    gen = ["--generate-setup", "echo setup", "--produces", ".claude/skills/rev"]

    res = _cli("add", "s/skill/rev", "--generate-command", "echo one", *gen)
    assert res.exit_code == 0, res.output
    assert "Updated s/skill/rev: generate unset → echo one" in _text(res)
    spec = _comp(proj, "rev").generate
    assert spec is not None and spec.command == ["echo", "one"]
    assert spec.setup == [["echo", "setup"]] and spec.produces == [".claude/skills/rev"]

    res = _cli("add", "s/skill/rev", "--generate-command", "echo two", *gen)
    assert res.exit_code == 0, res.output
    assert "Updated s/skill/rev: generate echo one → echo two" in _text(res)

    cfg = (proj / ".agentry.yml").read_bytes()
    res = _cli("add", "s/skill/rev", "--generate-command", "echo two", *gen)
    assert res.exit_code == 0 and "s/skill/rev is already declared — no change." in _text(res)
    assert (proj / ".agentry.yml").read_bytes() == cfg


def test_update_component_on_undeclared_ref_changes_nothing(tmp_path: Path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    store = ConfigStore.load(proj)
    before = store.dumps()
    assert store.update_component("s/command/nope", {"path": "alt/c.md"}) == {}
    assert store.dumps() == before
