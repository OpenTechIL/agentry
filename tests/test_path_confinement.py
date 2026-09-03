"""Path-confinement and URL-scheme guards.

Destination templates and source URLs can arrive from a *remote* catalog
(``target_profiles``, catalog ``targets`` overlays, catalog repo entries), so they are
untrusted input that ends up joined onto the project root or handed to ``git clone``.
These tests pin the two layers of defence: model validation at parse time, and the
``confined``/``require_confined`` join guard at the filesystem boundary.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentry.installers._paths import PathEscapeError, confined, require_confined
from agentry.models import ProfileRule, Source, SourceType, Strategy

# -- model validation -----------------------------------------------------


@pytest.mark.parametrize("bad", ["/etc/cron.d/x", "../../escape", "a/../../b", "C:/Windows/x"])
def test_profile_rule_dest_must_stay_inside_project(bad):
    with pytest.raises(ValidationError, match="relative path inside the project"):
        ProfileRule(strategy=Strategy.LINK, dest=bad)


@pytest.mark.parametrize("bad", ["/etc/hosts", "../../.zshrc", r"C:\Windows\x"])
def test_profile_rule_file_must_stay_inside_project(bad):
    with pytest.raises(ValidationError, match="relative path inside the project"):
        ProfileRule(strategy=Strategy.MERGE, file=bad, pointer="mcpServers")


def test_profile_rule_accepts_ordinary_templates():
    rule = ProfileRule(strategy=Strategy.LINK, dest=".myide/skills/{name}")
    assert rule.dest == ".myide/skills/{name}"


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh -c 'touch /tmp/pwned'",
        "--upload-pack=/bin/sh",
        "ftp://example.com/repo.git",
    ],
)
def test_git_source_rejects_unsupported_url_schemes(url):
    with pytest.raises(ValidationError, match="unsupported URL scheme"):
        Source(name="s", type=SourceType.GIT, url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/r",
        "ssh://git@github.com/o/r.git",
        "git@github.com:o/r.git",
        "/srv/mirrors/r.git",
        "./local-clone",
    ],
)
def test_git_source_accepts_real_transports(url):
    assert Source(name="s", type=SourceType.GIT, url=url).url == url


# -- the join guard -------------------------------------------------------


def test_confined_allows_a_nested_destination(tmp_path):
    assert confined(tmp_path, ".claude/skills/x") == tmp_path / ".claude/skills/x"


@pytest.mark.parametrize("bad", ["../outside", "../../outside", "."])
def test_confined_refuses_escapes_and_the_root_itself(tmp_path, bad):
    assert confined(tmp_path, bad) is None


def test_confined_does_not_follow_the_managed_symlink_it_is_checking(tmp_path):
    """A managed link points *out* of the project (into the store or a local source).

    Resolving the final component would follow it out and reject agentry's own install,
    so only the parent is resolved. This is the regression that made every link test fail.
    """
    outside = tmp_path / "elsewhere" / "skill"
    outside.mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".claude/skills").mkdir(parents=True)
    (project / ".claude/skills/x").symlink_to(outside)

    assert confined(project, ".claude/skills/x") == project / ".claude/skills/x"


def test_require_confined_raises_on_escape(tmp_path):
    with pytest.raises(PathEscapeError, match="outside the project root"):
        require_confined(tmp_path, "../../etc/passwd")
