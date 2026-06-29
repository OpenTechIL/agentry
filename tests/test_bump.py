# tests/test_bump.py
import pytest
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
