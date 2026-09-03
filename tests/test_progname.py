"""Command naming: the three entry points, `--version`, and the `agy` collision notice.

agentry installs as ``agentry`` (canonical) plus ``agy`` and ``agyx``. ``agy`` is also the
command for Google's Antigravity CLI, so hints interpolate the *invoked* name and both the
CLI and ``doctor`` call out the ambiguity when PATH resolves ``agy`` elsewhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib
from typer.testing import CliRunner

from agentry import __version__, doctor
from agentry.cli import app
from agentry.config import ConfigStore
from agentry.progname import CANONICAL, prog

runner = CliRunner()


def test_all_three_names_are_declared_as_console_scripts():
    scripts = tomllib.loads(Path("pyproject.toml").read_text())["project"]["scripts"]
    assert set(scripts) == {"agentry", "agy", "agyx"}
    assert set(scripts.values()) == {"agentry.cli:app"}


def test_version_flag_matches_the_version_subcommand():
    flag = runner.invoke(app, ["--version"])
    sub = runner.invoke(app, ["version"])
    assert flag.exit_code == 0
    assert flag.output.strip() == sub.output.strip() == f"agentry {__version__}"


# -- prog() ---------------------------------------------------------------


def test_prog_returns_the_invoked_alias(monkeypatch):
    for name in ("agentry", "agy", "agyx"):
        monkeypatch.setattr("sys.argv", [f"/usr/local/bin/{name}", "sync"])
        assert prog() == name


def test_prog_falls_back_to_canonical_for_unknown_argv0(monkeypatch):
    """Under pytest, `python -m`, or a wrapper, argv[0] isn't a command to retype."""
    for argv0 in ("/usr/bin/pytest", "-c", "", "/opt/wrapper.sh"):
        monkeypatch.setattr("sys.argv", [argv0])
        assert prog() == CANONICAL


def test_hints_name_the_invoked_alias(monkeypatch, tmp_path):
    """A user who typed `agyx` should not be told to run `agy`."""
    monkeypatch.setattr("sys.argv", ["/usr/local/bin/agyx"])
    with pytest.raises(FileNotFoundError, match=r"`agyx init`"):
        ConfigStore.load(tmp_path)


def test_missing_config_hint_explains_the_project_root_rule(monkeypatch, tmp_path):
    """The most common first-run failure: agentry reads config from the cwd, not upwards."""
    monkeypatch.setattr("sys.argv", ["/usr/local/bin/agentry"])
    with pytest.raises(FileNotFoundError, match="project root"):
        ConfigStore.load(tmp_path)


# -- collision detection --------------------------------------------------


def test_doctor_flags_an_agy_from_a_different_install(monkeypatch, tmp_path):
    ours = tmp_path / "ours"
    theirs = tmp_path / "theirs"
    for d in (ours, theirs):
        d.mkdir()
    (ours / "agentry").write_text("#!/bin/sh\n")
    (theirs / "agy").write_text("#!/bin/sh\n")
    monkeypatch.setattr("sys.argv", [str(ours / "agentry")])
    monkeypatch.setattr(doctor.shutil, "which", lambda name: str(theirs / "agy"))
    check = doctor.command_name_check()
    assert check is not None
    assert check.level == "warn" and check.category == "command"
    assert "Antigravity" in check.message and "agyx" in check.message


def test_doctor_is_quiet_when_agy_sits_beside_us(monkeypatch, tmp_path):
    """The three names are *separate* wrapper scripts in one bin dir.

    Comparing files rather than directories flagged every ordinary pip/uv install as a
    conflict, because `agentry` and `agy` are distinct console-script files.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("agentry", "agy"):
        (bindir / name).write_text("#!/bin/sh\n")
    monkeypatch.setattr("sys.argv", [str(bindir / "agentry")])
    monkeypatch.setattr(doctor.shutil, "which", lambda name: str(bindir / "agy"))
    assert doctor.command_name_check() is None


def test_doctor_is_quiet_when_agy_is_absent(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.command_name_check() is None


# -- driver-overlay consent ----------------------------------------------


def test_overlay_description_uses_readable_type_and_strategy_names():
    """The prompt is what a user reads before authorizing writes to their repo.

    Enum `str()` yields `ComponentType.SKILL`; the destinations table must show `skill`.
    """
    from agentry.cli import _describe_overlay
    from agentry.models import ComponentType, ProfileRule, Strategy

    lines = _describe_overlay(
        {
            ComponentType.SKILL: ProfileRule(strategy=Strategy.LINK, dest=".myide/skills/{name}"),
            ComponentType.MCP: ProfileRule(
                strategy=Strategy.MERGE, file=".myide/config.json", pointer="mcpServers"
            ),
        }
    )
    joined = "\n".join(lines)
    assert "ComponentType" not in joined
    assert "skill" in joined and "link" in joined and ".myide/skills/{name}" in joined
    assert "mcp" in joined and "merge" in joined and ".myide/config.json" in joined
