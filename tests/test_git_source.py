from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agentry.config import ConfigStore
from agentry.lockfile import load_lock
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import sync


def test_git_resolve_and_update(project: Path, git_source: Path):
    store = ConfigStore.load(project)
    store.add_source(Source(name="g", type=SourceType.GIT, url=f"file://{git_source}", ref="main"))
    store.add_component(Component(source="g", type=ComponentType.SKILL, name="code-reviewer"))
    store.save()

    sync(project)
    sha1 = load_lock(project).entry("g").resolved
    assert len(sha1) == 40
    link = project / ".claude/skills/code-reviewer"
    assert link.is_symlink() and (link / "SKILL.md").exists()

    # Advance the source by a commit.
    (git_source / "skills" / "code-reviewer" / "SKILL.md").write_text("# v2\n")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    subprocess.run(["git", "commit", "-qam", "v2"], cwd=git_source, check=True, env=env)

    # sync (no update) keeps the pinned SHA.
    sync(project)
    assert load_lock(project).entry("g").resolved == sha1

    # update advances the lock and the content.
    sync(project, update=True)
    sha2 = load_lock(project).entry("g").resolved
    assert sha2 != sha1
    assert (link / "SKILL.md").read_text() == "# v2\n"
