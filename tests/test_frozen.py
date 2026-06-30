"""Tests for `agy sync --frozen` — lockfile-only, drift-rejecting installs.

`--frozen` is the reproducible-CI guarantee: install strictly from `.agentry.lock`, never
re-resolve, and abort if any source is unpinned or has drifted from its locked value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import sync
from agentry.resolver import ResolveError


def _project_with_source(proj: Path, src: Path) -> None:
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.SKILL, name="code-reviewer"))
    store.save()


def test_frozen_fails_when_source_unpinned(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    _project_with_source(proj, local_source)
    # No prior sync → "s" has no lock entry, so frozen cannot be satisfied.
    with pytest.raises(ResolveError, match="not pinned"):
        sync(proj, frozen=True)


def test_frozen_is_noop_when_lock_matches(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    _project_with_source(proj, local_source)
    sync(proj)  # pins the current content hash into .agentry.lock
    res = sync(proj, frozen=True)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert (proj / ".claude/skills/code-reviewer").is_symlink()


def test_frozen_rejects_drifted_source(tmp_path: Path, local_source: Path):
    proj = tmp_path / "proj"
    _project_with_source(proj, local_source)
    sync(proj)  # pin the current content hash
    # Mutate the local source so its content hash no longer matches the lock.
    (local_source / "skills" / "code-reviewer" / "SKILL.md").write_text("# changed\n")
    with pytest.raises(ResolveError, match="drifted"):
        sync(proj, frozen=True)
