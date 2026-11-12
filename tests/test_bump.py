# tests/test_bump.py
import subprocess

import pytest
import scripts.bump as bump
from scripts.bump import bump_changelog, bump_init, bump_pyproject, parse_version


def test_parse_version_accepts_semver():
    assert parse_version("1.2.3") == "1.2.3"


@pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "1.2.3a", "x"])
def test_parse_version_rejects_non_semver(bad):
    with pytest.raises(ValueError):
        parse_version(bad)


def test_bump_pyproject_updates_version_and_preserves_rest():
    text = '[project]\nname = "agentry"\nversion = "0.1.0"  # keep me\n'
    out = bump_pyproject(text, "0.2.0")
    assert 'version = "0.2.0"' in out
    assert 'name = "agentry"' in out
    assert "0.1.0" not in out


def test_bump_init_updates_dunder_version():
    text = '"""doc."""\n\n__version__ = "0.1.0"\n'
    out = bump_init(text, "0.2.0")
    assert '__version__ = "0.2.0"' in out
    assert "0.1.0" not in out


def test_bump_changelog_inserts_dated_section_and_fresh_unreleased():
    text = "# Changelog\n\n---\n\n## [Unreleased] — 2026-06-25\n\n### Added\n- a thing\n"
    out = bump_changelog(text, "0.2.0", "2026-06-29")
    assert "## [Unreleased]\n" in out
    assert "## [0.2.0] — 2026-06-29" in out
    # the new Unreleased heading sits above the dated one
    assert out.index("## [Unreleased]") < out.index("## [0.2.0]")
    # the old entry content is retained under the dated section
    assert "- a thing" in out


def test_bump_changelog_is_idempotent_guarded():
    text = "## [Unreleased]\n\n## [0.2.0] — 2026-06-01\n"
    with pytest.raises(ValueError):
        bump_changelog(text, "0.2.0", "2026-06-29")  # version already released


# --- T11: the release bump refreshes uv.lock before any git action -----------------------


def test_release_files_include_uv_lock():
    assert bump.UV_LOCK == bump.ROOT / "uv.lock"
    assert bump.UV_LOCK in bump.RELEASE_FILES


@pytest.fixture
def fake_release(tmp_path, monkeypatch):
    """Point every release file at a tmp copy and record (not run) subprocess calls."""
    files = {
        "PYPROJECT": ("pyproject.toml", '[project]\nname = "agentry"\nversion = "0.1.0"\n'),
        "INIT": ("__init__.py", '__version__ = "0.1.0"\n'),
        "CHANGELOG": ("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- a thing\n"),
        "UV_LOCK": ("uv.lock", 'version = 1\n[[package]]\nname = "agentry"\nversion = "0.1.0"\n'),
    }
    paths = {}
    for attr, (name, text) in files.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        monkeypatch.setattr(bump, attr, p)
        paths[attr] = p
    monkeypatch.setattr(
        bump,
        "RELEASE_FILES",
        (paths["PYPROJECT"], paths["INIT"], paths["CHANGELOG"], paths["UV_LOCK"]),
    )
    monkeypatch.setattr(bump, "_is_dirty", lambda: False)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(bump, "_run", lambda *args: calls.append(args))
    return paths, calls


def test_bump_runs_uv_lock_then_stages_it_before_commit_and_tag(fake_release):
    paths, calls = fake_release
    assert bump.main(["9.9.9"]) == 0

    assert [c[:2] for c in calls] == [
        ("uv", "lock"),
        ("git", "add"),
        ("git", "commit"),
        ("git", "tag"),
    ]
    assert str(paths["UV_LOCK"]) in calls[1]
    assert calls[3][:4] == ("git", "tag", "-a", "v9.9.9")
    assert 'version = "9.9.9"' in paths["PYPROJECT"].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "failure",
    [subprocess.CalledProcessError(1, ["uv", "lock"]), FileNotFoundError("uv")],
    ids=["uv-lock-fails", "uv-not-installed"],
)
def test_bump_aborts_before_any_git_action_when_uv_lock_fails(
    fake_release, monkeypatch, capsys, failure
):
    _, calls = fake_release

    def run(*args):
        calls.append(args)
        if args[:2] == ("uv", "lock"):
            raise failure

    monkeypatch.setattr(bump, "_run", run)
    assert bump.main(["9.9.9"]) == 1
    assert not any(c[0] == "git" for c in calls)
    assert "uv lock" in capsys.readouterr().err
