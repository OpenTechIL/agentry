from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.deps import pin_mismatch
from agentry.lockfile import load_lock, lock_path, save_lock
from agentry.models import Component, ComponentType, LockEntry, Source, SourceType
from agentry.reconcile import sync
from agentry.resolver import ResolveError


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
    (git_source / "skills" / "code-reviewer" / "SKILL.md").write_text("# v2\n", encoding="utf-8")
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
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "# v2\n"


# --- T7: a pin is only valid for the url/ref it was resolved from ------------------------

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@e.x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e.x",
}


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, env=_GIT_ENV, capture_output=True, text=True
    ).stdout.strip()


def _commit_skill(repo: Path, body: str) -> str:
    (repo / "skills" / "code-reviewer" / "SKILL.md").write_text(body, encoding="utf-8")
    _git("commit", "-qam", body.strip(), cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _git_project(project: Path, url: str, ref: str = "main") -> None:
    store = ConfigStore.load(project)
    store.add_source(Source(name="g", type=SourceType.GIT, url=url, ref=ref))
    store.add_component(Component(source="g", type=ComponentType.SKILL, name="code-reviewer"))
    store.save()


def _edit_config(project: Path, old: str, new: str) -> None:
    cfg = project / ".agentry.yml"
    text = cfg.read_text(encoding="utf-8")
    assert old in text
    cfg.write_text(text.replace(old, new, 1), encoding="utf-8")


def _skill_text(project: Path) -> str:
    return (project / ".claude/skills/code-reviewer/SKILL.md").read_text(encoding="utf-8")


def test_changing_source_url_repins(project: Path, git_source: Path, tmp_path: Path):
    """Editing a git source's url makes a plain `sync` re-clone the new origin and pin its
    HEAD — never fetch the old cached clone and keep the old SHA."""
    fork = tmp_path / "g3"
    _git("clone", "-q", str(git_source), str(fork), cwd=tmp_path)
    fork_sha = _commit_skill(fork, "# from fork\n")

    old_url, new_url = f"file://{git_source}", f"file://{fork}"
    _git_project(project, old_url)
    sync(project)
    old_sha = load_lock(project).entry("g").resolved

    _edit_config(project, f"url: {old_url}", f"url: {new_url}")
    sync(project)

    entry = load_lock(project).entry("g")
    assert entry.url == new_url
    assert entry.resolved == fork_sha != old_sha
    assert _git("remote", "get-url", "origin", cwd=project / ".agentry" / "g") == new_url
    assert _skill_text(project) == "# from fork\n"

    res = sync(project)  # settled: the next sync re-uses the new pin and changes nothing
    assert load_lock(project).entry("g").resolved == fork_sha
    assert res.created == [] and res.updated == [] and res.removed == []


def test_changing_source_ref_drops_the_old_pin(project: Path, git_source: Path):
    _git("checkout", "-qb", "dev", cwd=git_source)
    dev_sha = _commit_skill(git_source, "# dev\n")
    _git("checkout", "-q", "main", cwd=git_source)

    _git_project(project, f"file://{git_source}", ref="main")
    sync(project)
    main_sha = load_lock(project).entry("g").resolved

    _edit_config(project, "ref: main", "ref: dev")
    sync(project)

    entry = load_lock(project).entry("g")
    assert entry.ref == "dev"
    assert entry.resolved == dev_sha != main_sha
    assert _skill_text(project) == "# dev\n"


def test_unchanged_git_source_keeps_its_pin_and_is_a_noop(project: Path, git_source: Path):
    _git_project(project, f"file://{git_source}")
    sync(project)
    sha = load_lock(project).entry("g").resolved
    _commit_skill(git_source, "# upstream moved\n")  # a re-resolve would pick this up

    res = sync(project)
    assert load_lock(project).entry("g").resolved == sha
    assert res.created == [] and res.updated == [] and res.removed == []


def test_legacy_lock_without_ref_is_not_repinned(project: Path, git_source: Path):
    """A lock written by an older agentry has `"ref": null`; that is unknown, not changed."""
    _git_project(project, f"file://{git_source}")
    sync(project)
    lp = lock_path(project)
    payload = json.loads(lp.read_text(encoding="utf-8"))
    for e in payload["sources"]:
        e["ref"] = None
    lp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sha = load_lock(project).entry("g").resolved
    _commit_skill(git_source, "# upstream moved\n")

    res = sync(project)
    assert load_lock(project).entry("g").resolved == sha
    assert res.created == [] and res.updated == [] and res.removed == []


@pytest.mark.parametrize("field", ["url", "ref"])
def test_frozen_rejects_a_source_changed_since_locking(
    project: Path, git_source: Path, tmp_path: Path, field: str
):
    old_url = f"file://{git_source}"
    _git_project(project, old_url)
    sync(project)
    before = lock_path(project).read_bytes()

    if field == "url":
        fork = tmp_path / "g3"
        _git("clone", "-q", str(git_source), str(fork), cwd=tmp_path)
        _edit_config(project, f"url: {old_url}", f"url: file://{fork}")
    else:
        _git("branch", "dev", cwd=git_source)
        _edit_config(project, "ref: main", "ref: dev")

    with pytest.raises(ResolveError) as exc:
        sync(project, frozen=True)
    msg = str(exc.value)
    assert f"changed in .agentry.yml since it was locked ({field}: " in msg
    assert "re-pin" in msg
    assert lock_path(project).read_bytes() == before


def _entry(**kw) -> LockEntry:
    base = dict(name="g", type=SourceType.GIT, url="file:///a", ref="main", resolved="0" * 40)
    return LockEntry(**{**base, **kw})


def _git_src(**kw) -> Source:
    base = dict(name="g", type=SourceType.GIT, url="file:///a", ref="main")
    return Source(**{**base, **kw})


def test_pin_mismatch_matches_and_detects_each_change():
    assert pin_mismatch(_entry(), _git_src()) is None
    assert pin_mismatch(_entry(ref=None), _git_src(ref="dev")) is None  # legacy lock
    assert "url" in (pin_mismatch(_entry(), _git_src(url="file:///b")) or "")
    assert "ref" in (pin_mismatch(_entry(), _git_src(ref="dev")) or "")
    local = Source(name="g", type=SourceType.LOCAL, path="./x")
    assert "type" in (pin_mismatch(_entry(), local) or "")


def test_pin_mismatch_ignores_local_path_changes():
    entry = LockEntry(name="g", type=SourceType.LOCAL, path="./old", resolved="h")
    assert pin_mismatch(entry, Source(name="g", type=SourceType.LOCAL, path="./new")) is None


def _trust(project: Path) -> None:
    """Record consent for source ``g`` (what `agy trust g` does)."""
    lock = load_lock(project)
    lock.entry("g").trusted = True
    save_lock(project, lock)


def test_url_change_revokes_trust_same_sha(project: Path, git_source: Path, tmp_path: Path):
    """Trust is bound to the origin as well as the SHA: a fork sharing history (same SHA)
    is a different owner and must be re-trusted."""
    mirror = tmp_path / "mirror"
    _git("clone", "-q", str(git_source), str(mirror), cwd=tmp_path)
    old_url = f"file://{git_source}"
    _git_project(project, old_url)
    sync(project)
    _trust(project)
    sha = load_lock(project).entry("g").resolved

    _edit_config(project, f"url: {old_url}", f"url: file://{mirror}")
    sync(project)

    entry = load_lock(project).entry("g")
    assert entry.resolved == sha  # same content ...
    assert entry.trusted is False  # ... but a new origin: consent revoked


def test_ref_change_to_the_same_sha_keeps_trust(project: Path, git_source: Path):
    """Trust is about content: a ref rename that resolves to the same SHA keeps consent."""
    _git("branch", "dev", cwd=git_source)  # dev == main
    _git_project(project, f"file://{git_source}", ref="main")
    sync(project)
    _trust(project)
    sha = load_lock(project).entry("g").resolved

    _edit_config(project, "ref: main", "ref: dev")
    sync(project)

    entry = load_lock(project).entry("g")
    assert entry.ref == "dev" and entry.resolved == sha
    assert entry.trusted is True
