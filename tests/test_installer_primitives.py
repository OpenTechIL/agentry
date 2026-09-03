"""Direct unit tests for the installer primitives.

These modules were only exercised transitively through the reconcile-engine tests, which
means their edge cases — never-clobber refusals, ownership rules, empty-parent pruning,
plugin-root rewriting — were covered by accident rather than on purpose. Testing them
directly pins the contracts the engine relies on.
"""

from __future__ import annotations

import json

import pytest

from agentry.installers import copy as copy_inst
from agentry.installers import link as link_inst
from agentry.installers import link_merge as link_merge_inst
from agentry.installers._paths import prune_empty_parents
from agentry.installers.generate import confined
from agentry.resolver import STORE_DIR


@pytest.fixture
def store(tmp_path):
    """A project root with an agentry store holding one artifact."""
    artifact = tmp_path / STORE_DIR / "src" / "skills" / "hello"
    artifact.mkdir(parents=True)
    (artifact / "SKILL.md").write_text("hi\n")
    return tmp_path, artifact


# -- link -----------------------------------------------------------------


def test_install_link_creates_updates_and_is_idempotent(store):
    root, artifact = store
    assert link_inst.install_link(root, artifact, ".claude/skills/hello") == "created"
    assert link_inst.install_link(root, artifact, ".claude/skills/hello") == "exists"

    other = root / STORE_DIR / "src" / "skills" / "other"
    other.mkdir()
    assert link_inst.install_link(root, other, ".claude/skills/hello") == "updated"
    assert (root / ".claude/skills/hello").resolve() == other.resolve()


def test_links_are_relative_so_the_project_stays_portable(store):
    root, artifact = store
    link_inst.install_link(root, artifact, ".claude/skills/hello")
    import os

    target = os.readlink(root / ".claude/skills/hello")
    assert not os.path.isabs(target), target


def test_install_link_refuses_to_clobber_an_unmanaged_file(store):
    root, artifact = store
    dest = root / ".claude/skills"
    dest.mkdir(parents=True)
    (dest / "hello").write_text("hand-authored\n")

    with pytest.raises(FileExistsError, match="not managed by agentry"):
        link_inst.install_link(root, artifact, ".claude/skills/hello")
    assert (dest / "hello").read_text() == "hand-authored\n"


def test_install_link_refuses_to_clobber_a_foreign_symlink(store):
    root, artifact = store
    outside = root / "elsewhere"
    outside.mkdir()
    dest = root / ".claude/skills"
    dest.mkdir(parents=True)
    (dest / "hello").symlink_to(outside)

    with pytest.raises(FileExistsError, match="does not manage"):
        link_inst.install_link(root, artifact, ".claude/skills/hello")


def test_remove_link_only_removes_our_own(store):
    root, artifact = store
    outside = root / "elsewhere"
    outside.mkdir()
    (root / ".claude/skills").mkdir(parents=True)
    (root / ".claude/skills/foreign").symlink_to(outside)

    assert link_inst.remove_link(root, ".claude/skills/foreign") is False
    assert (root / ".claude/skills/foreign").is_symlink()

    link_inst.install_link(root, artifact, ".claude/skills/hello")
    assert link_inst.remove_link(root, ".claude/skills/hello") is True
    assert not (root / ".claude/skills/hello").exists()


def test_remove_link_is_a_noop_when_nothing_is_there(store):
    root, _ = store
    assert link_inst.remove_link(root, ".claude/skills/absent") is False


def test_link_state_reports_missing_drift_and_ok(store):
    root, artifact = store
    assert link_inst.link_state(root, artifact, ".claude/skills/hello") == "missing"

    link_inst.install_link(root, artifact, ".claude/skills/hello")
    assert link_inst.link_state(root, artifact, ".claude/skills/hello") == "ok"

    other = root / STORE_DIR / "src" / "skills" / "other"
    other.mkdir()
    assert link_inst.link_state(root, other, ".claude/skills/hello") == "drift"


# -- copy -----------------------------------------------------------------


def test_install_copy_creates_a_real_dir_not_a_symlink(store):
    root, artifact = store
    assert copy_inst.install_copy(root, artifact, ".x/hello", managed=False) == "created"
    dest = root / ".x/hello"
    assert dest.is_dir() and not dest.is_symlink()
    assert (dest / "SKILL.md").read_text() == "hi\n"


def test_install_copy_is_idempotent_then_updates_on_change(store):
    root, artifact = store
    copy_inst.install_copy(root, artifact, ".x/hello", managed=False)
    assert copy_inst.install_copy(root, artifact, ".x/hello", managed=True) == "exists"

    (artifact / "SKILL.md").write_text("changed\n")
    assert copy_inst.install_copy(root, artifact, ".x/hello", managed=True) == "updated"
    assert (root / ".x/hello/SKILL.md").read_text() == "changed\n"


def test_install_copy_of_a_single_file(store):
    root, _ = store
    f = root / STORE_DIR / "src" / "agents" / "planner.md"
    f.parent.mkdir(parents=True)
    f.write_text("plan\n")
    assert copy_inst.install_copy(root, f, ".x/planner.md", managed=False) == "created"
    assert (root / ".x/planner.md").read_text() == "plan\n"


def test_install_copy_refuses_an_unmanaged_path(store):
    root, artifact = store
    (root / ".x").mkdir()
    (root / ".x/hello").write_text("mine\n")
    with pytest.raises(FileExistsError, match="not managed by agentry"):
        copy_inst.install_copy(root, artifact, ".x/hello", managed=False)
    assert (root / ".x/hello").read_text() == "mine\n"


def test_remove_copy_and_state(store):
    root, artifact = store
    assert copy_inst.copy_state(root, artifact, ".x/hello") == "missing"
    copy_inst.install_copy(root, artifact, ".x/hello", managed=False)
    assert copy_inst.copy_state(root, artifact, ".x/hello") == "ok"

    (root / ".x/hello/SKILL.md").write_text("edited\n")
    assert copy_inst.copy_state(root, artifact, ".x/hello") == "drift"

    assert copy_inst.remove_copy(root, ".x/hello") is True
    assert copy_inst.remove_copy(root, ".x/hello") is False


# -- _paths ---------------------------------------------------------------


def test_prune_empty_parents_stops_at_the_root(tmp_path):
    deep = tmp_path / ".claude" / "skills" / "nested"
    deep.mkdir(parents=True)
    prune_empty_parents(tmp_path, deep)
    assert not (tmp_path / ".claude").exists()
    assert tmp_path.is_dir(), "must never remove the project root itself"


def test_prune_empty_parents_keeps_non_empty_dirs(tmp_path):
    deep = tmp_path / ".claude" / "skills" / "nested"
    deep.mkdir(parents=True)
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    prune_empty_parents(tmp_path, deep)
    assert not (tmp_path / ".claude" / "skills").exists()
    assert (tmp_path / ".claude" / "settings.json").is_file()


# -- confinement ----------------------------------------------------------


@pytest.mark.parametrize("rel", ["../outside", "../../etc/passwd", ".", "sub/../../outside"])
def test_confined_rejects_escapes(tmp_path, rel):
    assert confined(tmp_path, rel) is None


def test_confined_accepts_a_normal_nested_path(tmp_path):
    assert confined(tmp_path, "a/b/c") == tmp_path / "a/b/c"


# -- link_merge rewriting -------------------------------------------------


def test_plugin_root_refs_finds_variables_at_any_depth():
    fragment = {
        "SessionStart": [
            {"hooks": [{"command": "${CLAUDE_PLUGIN_ROOT}/hooks/start.sh"}]},
            {"hooks": [{"command": "echo plain"}]},
        ]
    }
    refs = link_merge_inst.plugin_root_refs(fragment)
    assert refs == ["${CLAUDE_PLUGIN_ROOT}/hooks/start.sh"]


def test_plugin_root_refs_matches_any_harness_prefix():
    assert link_merge_inst.plugin_root_refs({"c": "${SOME_PLUGIN_ROOT}/x"})
    assert link_merge_inst.plugin_root_refs({"c": "${PLUGIN_ROOT}/x"})
    assert not link_merge_inst.plugin_root_refs({"c": "${CLAUDE_PROJECT_DIR}/x"})


def test_rewrite_strings_walks_nested_structures():
    before = {"a": ["${R}/x", {"b": "${R}/y"}], "c": 3, "d": None}
    after = link_merge_inst._rewrite_strings(before, "${R}", "/abs")
    assert after == {"a": ["/abs/x", {"b": "/abs/y"}], "c": 3, "d": None}
    assert before["a"][0] == "${R}/x", "must not mutate the input"


def test_rewrite_strings_with_an_empty_prefix_is_a_noop():
    assert link_merge_inst._rewrite_strings({"a": "x"}, "", "/abs") == {"a": "x"}


def test_collect_leftovers_reports_unrewritten_paths():
    out: list[str] = []
    link_merge_inst._collect_leftovers({"a": "${R}/still-here", "b": ["/abs/fine"]}, "${R}", out)
    assert out == ["${R}/still-here"]


def test_load_fragment_round_trips_json(tmp_path):
    from agentry.installers.merge import load_fragment

    p = tmp_path / "hooks.json"
    p.write_text(json.dumps({"SessionStart": [{"hooks": []}]}))
    assert load_fragment(p) == {"SessionStart": [{"hooks": []}]}
