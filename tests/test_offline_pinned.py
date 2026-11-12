"""T23: a git source whose pinned SHA is already in the local store clone is not fetched.

`sync`, `status`, `doctor` and `sync --frozen` then work offline (origin unreachable). A fetch
still happens when it is needed: the pinned SHA is missing from the clone, `update` names the
source, or the source's url changed (T7). A scoped `update a` never fetches `b`.
Hermetic: local ``file://`` git repos; network access is never attempted.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agentry import resolver
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.lockfile import load_lock, lock_path, save_lock
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync

_C = ComponentType
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@e.x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e.x",
}


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, env=_ENV, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, name: str, body: str = "v1") -> tuple[Path, str]:
    repo = tmp_path / f"repo-{name}"
    (repo / "skills" / f"s{name}").mkdir(parents=True)
    (repo / "skills" / f"s{name}" / "SKILL.md").write_text(f"# s{name}\n{body}\n", "utf-8")
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "init")
    return repo, f"file://{repo}"


def _advance(repo: Path, name: str, body: str) -> str:
    (repo / "skills" / f"s{name}" / "SKILL.md").write_text(f"# s{name}\n{body}\n", "utf-8")
    _run(repo, "commit", "-qam", body)
    return _run(repo, "rev-parse", "HEAD")


def _project(tmp_path: Path, sources: dict[str, str]) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    for name, url in sources.items():
        store.add_source(Source(name=name, type=SourceType.GIT, url=url, ref="main"))
        store.add_component(Component(source=name, type=_C.SKILL, name=f"s{name}"))
    store.save()
    return proj


def _skill(proj: Path, name: str) -> str:
    return (proj / f".claude/skills/s{name}/SKILL.md").read_text(encoding="utf-8")


@pytest.fixture
def git_calls(monkeypatch):
    """Record every git subcommand agentry runs, as (subcommand, cwd-name)."""
    calls: list[tuple[str, str]] = []
    real = resolver._git

    def spy(args, cwd=None):
        calls.append((args[0], Path(cwd).name if cwd else Path(args[-1]).name))
        return real(args, cwd=cwd)

    monkeypatch.setattr(resolver, "_git", spy)
    return calls


def _network(calls, source: str | None = None) -> list[tuple[str, str]]:
    """The fetch/clone calls (optionally for one source's store dir)."""
    return [c for c in calls if c[0] in ("fetch", "clone") and (source is None or c[1] == source)]


@pytest.fixture
def one_source(tmp_path: Path):
    repo, url = _repo(tmp_path, "a")
    proj = _project(tmp_path, {"a": url})
    sync(proj)
    return proj, repo


def _take_offline(repo: Path, tmp_path: Path) -> None:
    repo.rename(tmp_path / "unreachable")  # any fetch/clone of the origin now fails


# --- pinned and present: no fetch, works offline --------------------------------------------


def test_sync_with_pin_present_does_not_fetch(one_source, git_calls):
    proj, _ = one_source
    sync(proj)
    assert _network(git_calls) == []


def test_sync_with_pin_present_works_offline(one_source, tmp_path):
    proj, repo = one_source
    lock_before = lock_path(proj).read_bytes()
    _take_offline(repo, tmp_path)
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []  # idempotent
    assert lock_path(proj).read_bytes() == lock_before
    assert "v1" in _skill(proj, "a")


def test_status_works_offline(one_source, tmp_path):
    proj, repo = one_source
    _take_offline(repo, tmp_path)
    rows, _ = status(proj)
    assert rows and all(r.state == "ok" for r in rows)


def test_doctor_works_offline(one_source, tmp_path):
    proj, repo = one_source
    _take_offline(repo, tmp_path)
    assert not [c for c in run_checks(proj) if c.level == "error"]


def test_frozen_sync_works_offline(one_source, tmp_path):
    proj, repo = one_source
    lock_before = lock_path(proj).read_bytes()
    _take_offline(repo, tmp_path)
    sync(proj, frozen=True)
    assert lock_path(proj).read_bytes() == lock_before


def test_offline_sync_is_idempotent(one_source, tmp_path):
    proj, repo = one_source
    _take_offline(repo, tmp_path)
    sync(proj)
    before = (proj / ".claude/skills/sa/SKILL.md").read_bytes()
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert (proj / ".claude/skills/sa/SKILL.md").read_bytes() == before


# --- cases that must still fetch ----------------------------------------------------------


def test_pinned_sha_missing_from_clone_is_fetched(one_source, git_calls):
    """A teammate's lock pins a commit this clone hasn't seen: it must be fetched."""
    proj, repo = one_source
    new_sha = _advance(repo, "a", "v2")
    lock = load_lock(proj)
    lock.entry("a").resolved = new_sha
    save_lock(proj, lock)

    sync(proj)
    assert _network(git_calls, "a")
    assert "v2" in _skill(proj, "a")


def test_missing_store_clone_is_cloned(one_source, git_calls):
    import shutil

    proj, _ = one_source
    shutil.rmtree(proj / ".agentry" / "a")
    sync(proj)
    assert ("clone", "a") in git_calls
    assert "v1" in _skill(proj, "a")


def test_update_naming_the_source_fetches(one_source, git_calls):
    proj, repo = one_source
    new_sha = _advance(repo, "a", "v2")
    sync(proj, update=True, update_only=frozenset({"a"}))
    assert _network(git_calls, "a")
    assert load_lock(proj).entry("a").resolved == new_sha


def test_bare_update_fetches_every_source(tmp_path, git_calls):
    _, url_a = _repo(tmp_path, "a")
    _, url_b = _repo(tmp_path, "b")
    proj = _project(tmp_path, {"a": url_a, "b": url_b})
    sync(proj)
    git_calls.clear()
    sync(proj, update=True)
    assert _network(git_calls, "a") and _network(git_calls, "b")


def test_scoped_update_does_not_fetch_unnamed_source(tmp_path, git_calls):
    repo_a, url_a = _repo(tmp_path, "a")
    _, url_b = _repo(tmp_path, "b")
    proj = _project(tmp_path, {"a": url_a, "b": url_b})
    sync(proj)
    git_calls.clear()
    _advance(repo_a, "a", "v2")
    sync(proj, update=True, update_only=frozenset({"a"}))
    assert _network(git_calls, "a")
    assert _network(git_calls, "b") == []


def test_url_change_reclones_the_source(one_source, tmp_path, git_calls):
    """T7: a new url is a new identity, so the pin doesn't save the fetch."""
    proj, repo = one_source
    fork = tmp_path / "fork-a"
    _run(tmp_path, "clone", "-q", str(repo), str(fork))
    fork_sha = _advance(fork, "a", "fork")
    cfg = proj / ".agentry.yml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(f"file://{repo}", f"file://{fork}"), "utf-8"
    )
    sync(proj)
    assert ("clone", "a") in git_calls
    assert load_lock(proj).entry("a").resolved == fork_sha


def test_ref_change_fetches_the_source(one_source, git_calls):
    """T7: a ref edit drops the pin, so the source is fetched to resolve the new ref."""
    proj, repo = one_source
    _run(repo, "checkout", "-qb", "dev")
    dev_sha = _advance(repo, "a", "dev")
    _run(repo, "checkout", "-q", "main")
    cfg = proj / ".agentry.yml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("ref: main", "ref: dev"), "utf-8")
    sync(proj)
    assert _network(git_calls, "a")
    assert load_lock(proj).entry("a").resolved == dev_sha


def test_pinned_sha_gone_from_origin_errors_and_changes_nothing(one_source):
    """The lock pins a commit neither the clone nor origin has (force-pushed away): after
    one fetch, a clear error points at `update`, and nothing is written."""
    from agentry.resolver import ResolveError

    proj, _ = one_source
    gone = "0123456789abcdef0123456789abcdef01234567"
    lock = load_lock(proj)
    lock.entry("a").resolved = gone
    save_lock(proj, lock)
    lock_before = lock_path(proj).read_bytes()
    skill_before = (proj / ".claude/skills/sa/SKILL.md").read_bytes()

    with pytest.raises(ResolveError) as exc:
        sync(proj)
    assert str(exc.value) == (
        f"source 'a': locked commit {gone[:12]} is no longer on origin (force-pushed or "
        "deleted?). Run `agentry update a` to re-pin."
    )
    assert lock_path(proj).read_bytes() == lock_before
    assert (proj / ".claude/skills/sa/SKILL.md").read_bytes() == skill_before


def test_scoped_refusal_restore_does_not_fetch(tmp_path, git_calls):
    """`update a` refused by rule (b): a is fetched once (it's named); pinned b is never
    fetched, and _restore_checkouts re-checks a out at its pin without another fetch."""
    import json

    from agentry.deps import DependencyError

    def descriptor(repo: Path, skills: list[dict]) -> None:
        (repo / "agentry.yaml").write_text(
            json.dumps({"version": 1, "provides": {"skill": skills}}), encoding="utf-8"
        )

    repo_b, url_b = _repo(tmp_path, "b")
    descriptor(repo_b, [{"name": "sb", "path": "skills/sb"}])
    _run(repo_b, "add", "-A")
    _run(repo_b, "commit", "-qm", "descriptor")
    repo_a, url_a = _repo(tmp_path, "a")
    req = [{"type": "skill", "name": "sb", "url": url_b, "ref": "main"}]
    descriptor(repo_a, [{"name": "sa", "path": "skills/sa", "requires": req}])
    _run(repo_a, "add", "-A")
    _run(repo_a, "commit", "-qm", "requires sb")
    proj = _project(tmp_path, {"a": url_a, "b": url_b})
    sync(proj)

    req = [{"type": "skill", "name": "missing", "url": url_b, "ref": "main"}]
    descriptor(repo_a, [{"name": "sa", "path": "skills/sa", "requires": req}])
    _run(repo_a, "commit", "-qam", "requires b/missing")
    git_calls.clear()

    with pytest.raises(DependencyError):
        sync(proj, update=True, update_only=frozenset({"a"}))
    assert _network(git_calls, "a") == [("fetch", "a")]  # the update's fetch, not the restore's
    assert _network(git_calls, "b") == []
    assert "v1" in _skill(proj, "a")


def test_fresh_clone_with_pin_gone_from_origin_errors_and_keeps_lock(one_source, monkeypatch):
    """No store clone yet (fresh checkout) and the lock pins a commit origin no longer has:
    after cloning, the same friendly error points at `update`, exit 1, lock unchanged."""
    import shutil

    from typer.testing import CliRunner

    from agentry.cli import app

    proj, _ = one_source
    shutil.rmtree(proj / ".agentry" / "a")
    gone = "fedcba9876543210fedcba9876543210fedcba98"
    lock = load_lock(proj)
    lock.entry("a").resolved = gone
    save_lock(proj, lock)
    lock_before = lock_path(proj).read_bytes()

    monkeypatch.chdir(proj)
    result = CliRunner().invoke(app, ["sync"])
    assert result.exit_code == 1
    assert (
        f"source 'a': locked commit {gone[:12]} is no longer on origin (force-pushed or "
        "deleted?). Run `agentry update a` to re-pin."
    ) in " ".join((result.output + result.stderr).split())
    assert lock_path(proj).read_bytes() == lock_before
