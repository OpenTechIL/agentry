"""#46: prompts must not trust ``sys.stdin.isatty()`` alone.

On Windows a NUL stdin (``< NUL``, a CI runner's console) reports ``isatty() == True`` but
reads EOF at once, so ``agentry add <repo>`` showed the picker, hit EOF and exited 1. These
tests simulate that on any OS with fake streams, whose ``isatty()`` we choose, swapped into
``sys`` for one in-process CLI run. The expected outcome in every degraded case is the
documented non-interactive one: exactly what the same command does with a plain non-TTY
stdin (the baseline run in each test).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry import cli
from agentry.cli import app
from agentry.config import ConfigStore

_PICKER = "component(s) available:"


class _Stream(io.StringIO):
    def __init__(self, text: str = "", *, tty: bool) -> None:
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _run(monkeypatch, args: list[str], *, stdin_tty: bool, stdout_tty: bool, text: str = ""):
    """Run the CLI in-process with fake std streams; return (exit code, combined output)."""
    out, errs = _Stream(tty=stdout_tty), _Stream(tty=stdout_tty)
    with monkeypatch.context() as m:
        m.setenv("COLUMNS", "200")
        # A fake TTY stands for a real console on every OS (on Windows the fix also probes
        # the console handle, which a StringIO doesn't have).
        m.setattr(cli, "_windows_console", lambda fd: stdin_tty, raising=False)
        m.setattr(sys, "stdin", _Stream(text, tty=stdin_tty))
        m.setattr(sys, "stdout", out)
        m.setattr(sys, "stderr", errs)
        try:
            app(args, prog_name="agentry")
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        except Exception as exc:  # noqa: BLE001 — a crash is the failure mode under test
            return f"crashed: {type(exc).__name__}", out.getvalue() + errs.getvalue()
    return code, " ".join((out.getvalue() + errs.getvalue()).split())


# --- fixtures ------------------------------------------------------------------------------


def _catalog_project(tmp_path: Path, monkeypatch) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "kit"
    for stem in ("alpha", "beta"):
        p = repo / "commands" / f"{stem}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {stem}\n", encoding="utf-8")
    catalog = tmp_path / "repositories.json"
    catalog.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": {
                    "kit": {"summary": "k", "source": {"type": "local", "path": str(repo)}}
                },
            }
        ),
        encoding="utf-8",
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, ["claude"]).save()
    monkeypatch.chdir(proj)
    assert CliRunner().invoke(app, ["catalog", "add", "r", str(catalog)]).exit_code == 0
    return proj


def _added(proj: Path) -> list[str]:
    return sorted(c.ref for c in ConfigStore.load(proj).parsed().components if c.source == "kit")


def _baseline_add(tmp_path: Path, monkeypatch) -> tuple[int, list[str]]:
    """`add kit` with a plain non-TTY stdin: the documented non-interactive outcome."""
    proj = _catalog_project(tmp_path / "baseline", monkeypatch)
    code, out = _run(monkeypatch, ["add", "kit"], stdin_tty=False, stdout_tty=False)
    assert _PICKER not in out
    return code, _added(proj)


# --- (a) (b) (c): `agentry add <repo>` -----------------------------------------------------


def test_baseline_non_tty_add_installs_everything(tmp_path: Path, monkeypatch):
    """Guard: the documented non-interactive outcome is "install all", exit 0."""
    code, added = _baseline_add(tmp_path, monkeypatch)
    assert code == 0
    assert added == ["kit/command/alpha", "kit/command/beta"]


def test_tty_stdin_at_eof_does_not_crash_the_picker(tmp_path: Path, monkeypatch):
    """(a) stdin claims a TTY but reads EOF at once (Windows NUL), stdout is a TTY too: no
    crash, the non-interactive outcome, and the run still says what it installed."""
    want = _baseline_add(tmp_path, monkeypatch)
    proj = _catalog_project(tmp_path / "nul", monkeypatch)
    code, out = _run(monkeypatch, ["add", "kit"], stdin_tty=True, stdout_tty=True, text="")
    assert (code, _added(proj)) == want, out
    assert "Traceback" not in out and "Aborted" not in out
    assert "Added kit" in out
    assert (
        "no input — installing all 2 component(s). To choose, use `agentry add kit@name[,name]` "
        "or `--type <type>`."
    ) in out


def test_tty_stdin_with_non_tty_stdout_skips_the_picker(tmp_path: Path, monkeypatch):
    """(b) stdin claims a TTY, stdout is redirected (a script/CI log): never prompt."""
    want = _baseline_add(tmp_path, monkeypatch)
    proj = _catalog_project(tmp_path / "redir", monkeypatch)
    code, out = _run(monkeypatch, ["add", "kit"], stdin_tty=True, stdout_tty=False, text="")
    assert _PICKER not in out
    assert (code, _added(proj)) == want, out


def test_real_interactive_picker_still_works(tmp_path: Path, monkeypatch):
    """(c) Guard: both ends are TTYs and the user answers — the picker is shown and honoured."""
    proj = _catalog_project(tmp_path, monkeypatch)
    code, out = _run(monkeypatch, ["add", "kit"], stdin_tty=True, stdout_tty=True, text="2\n")
    assert code == 0, out
    assert _PICKER in out
    assert _added(proj) == ["kit/command/beta"]


def test_real_interactive_picker_default_installs_all(tmp_path: Path, monkeypatch):
    """(c) Guard: pressing Enter takes the default ('a' = all)."""
    proj = _catalog_project(tmp_path, monkeypatch)
    code, out = _run(monkeypatch, ["add", "kit"], stdin_tty=True, stdout_tty=True, text="\n")
    assert code == 0, out
    assert _added(proj) == ["kit/command/alpha", "kit/command/beta"]


# --- (d) the other prompt sites ------------------------------------------------------------


def _overlay_project(tmp_path: Path, monkeypatch) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    catalog = tmp_path / "overlays.json"
    catalog.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": {},
                "targets": {"myide": {"skill": {"strategy": "link", "dest": ".myide/s/{name}"}}},
            }
        ),
        encoding="utf-8",
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, ["claude"]).save()
    monkeypatch.chdir(proj)
    assert CliRunner().invoke(app, ["catalog", "add", "ov", str(catalog)]).exit_code == 0
    return proj


def _overlay_outcome(proj: Path, code, out: str):
    has = "myide" in ConfigStore.load(proj).parsed().target_profiles
    return code, has, "Refusing to add overlay 'myide' without confirmation." in out


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty"),
    [
        pytest.param(True, True, id="eof"),
        pytest.param(True, False, id="redirected"),
    ],
)
def test_target_add_overlay_degraded_tty_refuses_like_non_tty(
    tmp_path: Path, monkeypatch, stdin_tty: bool, stdout_tty: bool
):
    """`target add` without --yes: a TTY-claiming stdin that can't answer gets the same
    refusal as a non-TTY (exit 1, the --yes hint, nothing written) — not a click Abort."""
    base = _overlay_project(tmp_path / "base", monkeypatch)
    code, out = _run(monkeypatch, ["target", "add", "myide"], stdin_tty=False, stdout_tty=False)
    want = _overlay_outcome(base, code, out)
    assert want == (1, False, True)

    proj = _overlay_project(tmp_path / "nul", monkeypatch)
    code, out = _run(
        monkeypatch, ["target", "add", "myide"], stdin_tty=stdin_tty, stdout_tty=stdout_tty
    )
    assert _overlay_outcome(proj, code, out) == want, out
    assert _REFUSE in out


def test_target_add_overlay_interactive_confirm_still_works(tmp_path: Path, monkeypatch):
    """Guard: with both TTYs, answering 'y' installs the overlay."""
    proj = _overlay_project(tmp_path, monkeypatch)
    code, out = _run(
        monkeypatch, ["target", "add", "myide"], stdin_tty=True, stdout_tty=True, text="y\n"
    )
    assert code == 0, out
    assert "myide" in ConfigStore.load(proj).parsed().target_profiles


def _generator_project(tmp_path: Path, monkeypatch) -> Path:
    from agentry.models import Component, ComponentType, GeneratorSpec, Source, SourceType

    src = tmp_path / "gen"
    src.mkdir(parents=True)
    script = (
        "import os;os.makedirs('.claude/skills/fake', exist_ok=True);"
        "open('.claude/skills/fake/SKILL.md','w').write('# fake\\n')"
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="g", type=SourceType.LOCAL, path=str(src)))
    spec = GeneratorSpec(command=[sys.executable, "-c", script], produces=[".claude/skills/fake"])
    store.add_component(Component(source="g", type=ComponentType.SKILL, name="fake", generate=spec))
    store.save()
    monkeypatch.chdir(proj)
    return proj


def test_sync_trust_prompt_at_eof_declines_like_non_tty(tmp_path: Path, monkeypatch):
    """The generator trust prompt: EOF on a TTY-claiming stdin declines exactly as a non-TTY
    does (skipped with the 'not trusted' warning), instead of aborting the whole sync."""
    base = _generator_project(tmp_path / "base", monkeypatch)
    code, out = _run(monkeypatch, ["sync"], stdin_tty=False, stdout_tty=False)
    want = (code, (base / ".claude/skills/fake").exists(), "not trusted" in out)
    assert want == (0, False, True)

    proj = _generator_project(tmp_path / "nul", monkeypatch)
    code, out = _run(monkeypatch, ["sync"], stdin_tty=True, stdout_tty=True, text="")
    assert (code, (proj / ".claude/skills/fake").exists(), "not trusted" in out) == want, out


# --- a real process with stdin=DEVNULL -----------------------------------------------------


def test_subprocess_add_with_devnull_stdin_installs_all(tmp_path: Path, monkeypatch):
    """The real thing: `agentry add kit` in a child process with stdin=DEVNULL (NUL on
    Windows) and no selection flags — no picker, everything installed, exit 0."""
    proj = _catalog_project(tmp_path, monkeypatch)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "COLUMNS": "200"}
    run = subprocess.run(
        [sys.executable, "-c", "from agentry.cli import app; app()", "add", "kit"],
        cwd=proj,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert _PICKER not in run.stdout
    assert _added(proj) == ["kit/command/alpha", "kit/command/beta"]


# --- the detector itself, incl. the Windows NUL case on any OS ----------------------------


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty", "platform", "console", "want"),
    [
        (True, True, "linux", False, True),  # POSIX: both TTYs is enough
        (False, True, "linux", True, False),
        (True, False, "linux", True, False),  # (b) redirected stdout
        (True, True, "win32", True, True),  # a real Windows console
        (True, True, "win32", False, False),  # NUL: isatty() True, no console handle
    ],
)
def test_interactive_detector(monkeypatch, stdin_tty, stdout_tty, platform, console, want):
    monkeypatch.setattr(sys, "stdin", _Stream(tty=stdin_tty))
    monkeypatch.setattr(sys, "stdout", _Stream(tty=stdout_tty))
    monkeypatch.setattr(cli.sys, "platform", platform)
    monkeypatch.setattr(cli, "_windows_console", lambda fd: console)
    monkeypatch.setattr(_Stream, "fileno", lambda self: 0, raising=False)
    assert cli._interactive() is want


def test_interactive_detector_never_raises_on_streams_without_fileno(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Stream(tty=True))  # StringIO.fileno() raises
    monkeypatch.setattr(sys, "stdout", _Stream(tty=True))
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert cli._interactive() is False


# --- the approved plan's list, via the API backend documented ------------------------------

_REFUSE = (
    "Refusing to add overlay 'myide' without confirmation. Re-run with `--yes` to accept these "
    "destinations non-interactively."
)


def test_windows_nul_console_probe_false_add_installs_everything(tmp_path: Path, monkeypatch):
    """The NUL case end to end: both streams claim TTY, platform win32, no console handle."""
    want = _baseline_add(tmp_path, monkeypatch)
    proj = _catalog_project(tmp_path / "nul", monkeypatch)
    with monkeypatch.context() as m:
        m.setattr(cli.sys, "platform", "win32")
        m.setattr(_Stream, "fileno", lambda self: 0, raising=False)
        out_stream, err_stream = _Stream(tty=True), _Stream(tty=True)
        m.setattr(sys, "stdin", _Stream(tty=True))
        m.setattr(sys, "stdout", out_stream)
        m.setattr(sys, "stderr", err_stream)
        m.setattr(cli, "_windows_console", lambda fd: False)
        m.setenv("COLUMNS", "200")
        with pytest.raises(SystemExit) as exc:
            app(["add", "kit"], prog_name="agentry")
    out = out_stream.getvalue() + err_stream.getvalue()
    assert (exc.value.code or 0, _added(proj)) == want, out
    assert _PICKER not in out


def test_picker_prompt_eof_installs_all_with_note(tmp_path: Path, monkeypatch):
    proj = _catalog_project(tmp_path, monkeypatch)

    def eof(*a, **k):
        raise EOFError

    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", eof)
    code, out = _run(monkeypatch, ["add", "kit"], stdin_tty=False, stdout_tty=False)
    assert code == 0, out
    assert _added(proj) == ["kit/command/alpha", "kit/command/beta"]
    assert (
        "no input — installing all 2 component(s). To choose, use `agentry add kit@name[,name]` "
        "or `--type <type>`."
    ) in out


def _raiser(exc_type):
    def confirm(*a, **k):
        raise exc_type()

    return confirm


@pytest.mark.parametrize("exc_type", [EOFError, cli.typer.Abort], ids=["eof", "abort"])
def test_trust_confirm_no_input_declines(tmp_path: Path, monkeypatch, exc_type):
    proj = _generator_project(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", _raiser(exc_type))
    code, out = _run(monkeypatch, ["sync"], stdin_tty=False, stdout_tty=False)
    assert code == 0, out
    assert not (proj / ".claude/skills/fake").exists()
    assert "not trusted" in out


@pytest.mark.parametrize("exc_type", [EOFError, cli.typer.Abort], ids=["eof", "abort"])
def test_overlay_confirm_no_input_refuses(tmp_path: Path, monkeypatch, exc_type):
    proj = _overlay_project(tmp_path, monkeypatch)
    before = (proj / ".agentry.yml").read_bytes()
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", _raiser(exc_type))
    code, out = _run(monkeypatch, ["target", "add", "myide"], stdin_tty=False, stdout_tty=False)
    assert code == 1, out
    assert _REFUSE in out
    assert (proj / ".agentry.yml").read_bytes() == before


# --- (d) agents-md --agent "Write …?" confirm ---------------------------------------------

_BODY = "# AGENTS.md\n\nSynthesized by the fake agent.\n"
_NOT_WRITING = "Not writing AGENTS.md without confirmation. Re-run with `--yes` to apply."
_EMIT = ["emit", "agents-md", "--agent", "--allow-transform"]


def _agents_md_project(tmp_path: Path, monkeypatch, existing: str | None = None) -> Path:
    """A project with one skill and a deterministic transform command: a python one-liner
    that drains stdin (the prompt) and prints a fixed body."""
    from agentry.models import Component, ComponentType, Source, SourceType
    from conftest import make_source

    tmp_path.mkdir(parents=True, exist_ok=True)
    src = make_source(tmp_path / "team")
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="team", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="team", type=ComponentType.SKILL, name="code-reviewer"))
    script = f"import sys; sys.stdin.read(); sys.stdout.write({_BODY!r})"
    store.doc["transform"] = {"command": [sys.executable, "-c", script]}
    store.save()
    from agentry.reconcile import sync

    sync(proj)  # agents-md composes from installed components
    if existing is not None:
        (proj / "AGENTS.md").write_text(existing, encoding="utf-8")
    monkeypatch.chdir(proj)
    return proj


def _agents_md_state(proj: Path) -> bytes | None:
    p = proj / "AGENTS.md"
    return p.read_bytes() if p.exists() else None


@pytest.mark.parametrize("existing", [None, "# mine\n"], ids=["absent", "existing"])
@pytest.mark.parametrize("exc_type", [EOFError, cli.typer.Abort], ids=["eof", "abort"])
def test_agents_md_confirm_no_input_refuses(tmp_path: Path, monkeypatch, existing, exc_type):
    proj = _agents_md_project(tmp_path, monkeypatch, existing)
    before = _agents_md_state(proj)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", _raiser(exc_type))
    code, out = _run(monkeypatch, _EMIT, stdin_tty=False, stdout_tty=False)
    assert code == 1, out
    assert _NOT_WRITING in out
    assert _agents_md_state(proj) == before  # nothing written, a missing file stays missing


def test_agents_md_tty_stdin_at_eof_refuses(tmp_path: Path, monkeypatch):
    """The NUL shape through the real confirm: TTY-claiming streams, no input."""
    proj = _agents_md_project(tmp_path, monkeypatch)
    code, out = _run(monkeypatch, _EMIT, stdin_tty=True, stdout_tty=True, text="")
    assert code == 1, out
    assert _NOT_WRITING in out and "Traceback" not in out
    assert _agents_md_state(proj) is None


def test_agents_md_redirected_stdout_refuses_without_prompting(tmp_path: Path, monkeypatch):
    proj = _agents_md_project(tmp_path, monkeypatch, "# mine\n")
    code, out = _run(monkeypatch, _EMIT, stdin_tty=True, stdout_tty=False, text="y\n")
    assert code == 1, out  # the queued "y" is never read: no prompt without a terminal
    assert _NOT_WRITING in out and "Write AGENTS.md?" not in out
    assert _agents_md_state(proj) == b"# mine\n"


def test_agents_md_interactive_yes_writes(tmp_path: Path, monkeypatch):
    """Guard: both TTYs and "y" writes the synthesized body."""
    proj = _agents_md_project(tmp_path, monkeypatch)
    code, out = _run(monkeypatch, _EMIT, stdin_tty=True, stdout_tty=True, text="y\n")
    assert code == 0, out
    assert (proj / "AGENTS.md").read_text(encoding="utf-8") == _BODY


def test_agents_md_interactive_no_is_unchanged(tmp_path: Path, monkeypatch):
    """Guard: an explicit "n" still aborts with exit 0 and writes nothing."""
    proj = _agents_md_project(tmp_path, monkeypatch, "# mine\n")
    code, out = _run(monkeypatch, _EMIT, stdin_tty=True, stdout_tty=True, text="n\n")
    assert code == 0, out
    assert "Aborted; nothing written." in out
    assert _agents_md_state(proj) == b"# mine\n"


def test_agents_md_yes_flag_writes_non_interactively(tmp_path: Path, monkeypatch):
    proj = _agents_md_project(tmp_path, monkeypatch, "# mine\n")
    code, out = _run(monkeypatch, [*_EMIT, "--yes"], stdin_tty=False, stdout_tty=False)
    assert code == 0, out
    assert (proj / "AGENTS.md").read_text(encoding="utf-8") == _BODY


def _run_split(monkeypatch, args: list[str], *, tty: bool, text: str = ""):
    """Like _run, but keeps stdout and stderr apart (to pin which stream a line goes to)."""
    out, errs = _Stream(tty=tty), _Stream(tty=tty)
    with monkeypatch.context() as m:
        m.setenv("COLUMNS", "200")
        m.setattr(cli, "_windows_console", lambda fd: tty, raising=False)
        m.setattr(sys, "stdin", _Stream(text, tty=tty))
        m.setattr(sys, "stdout", out)
        m.setattr(sys, "stderr", errs)
        with pytest.raises(SystemExit) as exc:
            app(args, prog_name="agentry")
    flat = lambda s: " ".join(s.split())  # noqa: E731
    return exc.value.code or 0, flat(out.getvalue()), flat(errs.getvalue())


def test_agents_md_no_tty_refuses_on_stderr_no_file(tmp_path: Path, monkeypatch):
    """(1) Plain non-TTY run (CI): refusal on stderr, exit 1, AGENTS.md never created."""
    proj = _agents_md_project(tmp_path, monkeypatch)
    code, out, err = _run_split(monkeypatch, _EMIT, tty=False)
    assert code == 1
    assert _NOT_WRITING in err and _NOT_WRITING not in out
    assert not (proj / "AGENTS.md").exists()


def test_agents_md_fake_tty_eof_refuses_on_stderr_no_file(tmp_path: Path, monkeypatch):
    """(2) TTY-claiming streams, no input: the same refusal on stderr."""
    proj = _agents_md_project(tmp_path, monkeypatch)
    code, _, err = _run_split(monkeypatch, _EMIT, tty=True, text="")
    assert code == 1
    assert _NOT_WRITING in err
    assert not (proj / "AGENTS.md").exists()


def test_agents_md_interactive_no_creates_no_file(tmp_path: Path, monkeypatch):
    """(4) Interactive "n" with no AGENTS.md yet: aborted, exit 0, still no file."""
    proj = _agents_md_project(tmp_path, monkeypatch)
    code, out, _ = _run_split(monkeypatch, _EMIT, tty=True, text="n\n")
    assert code == 0
    assert "Aborted; nothing written." in out
    assert not (proj / "AGENTS.md").exists()
