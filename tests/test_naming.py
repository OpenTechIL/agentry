"""`repo_basename` must reduce any locator shape to a bare name.

Its result becomes a logical source name *and* is substituted into destination path
templates (`{repo}` in a link+merge `dest`), so a separator or drive letter surviving into
it produces an install path that escapes the project.

The Windows CI job caught exactly that: splitting on `/` only, a local source path like
`C:\\Users\\me\\hooksrc` came back whole, and `.claude/hooks/agentry/{repo}@{ref}/hooks`
expanded to `.claude/hooks/agentry/C:\\Users\\me\\hooksrc@main/hooks`. These cases run on
every platform so the next regression doesn't need a Windows runner to surface.
"""

from __future__ import annotations

import pytest

from agentry.naming import repo_basename


@pytest.mark.parametrize(
    ("locator", "expected"),
    [
        # URLs
        ("https://github.com/owner/repo", "repo"),
        ("https://github.com/owner/repo.git", "repo"),
        ("https://github.com/owner/repo/", "repo"),
        ("https://gitlab.com/group/sub/repo.git", "repo"),
        ("ssh://git@github.com/owner/repo.git", "repo"),
        # scp-style remotes
        ("git@github.com:owner/repo.git", "repo"),
        ("git@github.com:owner/repo", "repo"),
        # POSIX paths
        ("/Users/me/src/repo", "repo"),
        ("/Users/me/src/repo/", "repo"),
        ("../sibling", "sibling"),
        ("./repo.git", "repo"),
        # Windows paths — the regression
        (r"C:\Users\me\src\repo", "repo"),
        (r"C:\Users\me\src\repo\\", "repo"),
        (r"..\sibling", "sibling"),
        (r"\\server\share\repo", "repo"),
        ("C:repo", "repo"),
        # file:// URLs built by naive f-strings, as the test suite does
        ("file:///Users/me/libb", "libb"),
        (r"file://C:\Users\me\libb", "libb"),
        # mixed separators
        (r"C:\src/mixed\repo", "repo"),
    ],
)
def test_reduces_every_locator_shape_to_a_bare_name(locator, expected):
    assert repo_basename(locator) == expected


@pytest.mark.parametrize(
    "locator",
    [
        "https://github.com/owner/repo",
        r"C:\Users\me\src\repo",
        r"\\server\share\repo",
        "git@github.com:owner/repo.git",
        "file:///Users/me/libb",
    ],
)
def test_result_is_always_safe_to_put_in_a_path_template(locator):
    """No separator, no drive letter — otherwise a `{repo}` dest escapes the project."""
    name = repo_basename(locator)
    assert "/" not in name
    assert "\\" not in name
    assert ":" not in name


def test_falls_back_when_nothing_usable_remains():
    assert repo_basename("/", fallback="dep") == "dep"
    assert repo_basename("", fallback="dep") == "dep"
    assert repo_basename("///", fallback="dep") == "dep"
    assert repo_basename("/") == ""


def test_strips_dot_git_only_as_a_suffix():
    assert repo_basename("https://h/o/my.git.repo") == "my.git.repo"
    assert repo_basename("https://h/o/gitrepo") == "gitrepo"


def test_link_merge_dest_vars_stay_inside_the_project_for_a_windows_local_path():
    """The end-to-end shape of the Windows CI failure, reproduced on any platform.

    `_link_merge_vars` feeds `{repo}` into a `dest` template. When a local source's path
    carried backslashes, `{repo}` expanded to the whole path and the resulting dest escaped
    the project — which `installers._paths.require_confined` then (correctly) refused.
    """
    from pathlib import Path

    from agentry.installers._paths import confined
    from agentry.models import Component, ComponentType, Source, SourceType
    from agentry.reconcile import _expand, _link_merge_vars

    src = Source(name="hooksrc", type=SourceType.LOCAL, path=r"C:\Users\me\tmp\hooksrc")
    comp = Component(source="hooksrc", type=ComponentType.HOOK, name="hooks")

    dest = _expand(".claude/hooks/agentry/{repo}@{ref}/{name}", _link_merge_vars(comp, src))
    assert dest == ".claude/hooks/agentry/hooksrc@main/hooks"
    assert confined(Path("/proj"), dest) is not None
