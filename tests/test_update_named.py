"""T20: `agentry update [SOURCE…]` re-resolves only the named sources.

Every unnamed source's lock entry stays byte-identical (trust included); an unknown name
fails before anything is resolved or written; a newly required dependency resolves; and a
dependency the named source now needs from a *pinned* unnamed source fails loudly with the
fix (`update A B`) instead of half-applying. Hermetic: local ``file://`` git repos only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.deps import DependencyError
from agentry.lockfile import load_lock, lock_path, save_lock
from agentry.models import Component, ComponentType, GeneratorSpec, Source, SourceType
from agentry.reconcile import sync

_C = ComponentType
runner = CliRunner()
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@e.x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e.x",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, env=_ENV, capture_output=True, text=True
    ).stdout.strip()


def _write_skill(repo: Path, name: str, body: str) -> None:
    d = repo / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {name}\n{body}\n", encoding="utf-8")


def _descriptor(repo: Path, skills: list[dict]) -> None:
    (repo / "agentry.yaml").write_text(
        json.dumps({"version": 1, "provides": {"skill": skills}}), encoding="utf-8"
    )


def _repo(tmp_path: Path, name: str, skills: dict[str, str], requires=None) -> tuple[Path, str]:
    """A git repo with skills {name: body}; `requires` maps skill -> list of requirements."""
    repo = tmp_path / f"repo-{name}"
    repo.mkdir()
    for skill, body in skills.items():
        _write_skill(repo, skill, body)
    _descriptor(
        repo,
        [
            {
                "name": s,
                "path": f"skills/{s}",
                **({"requires": requires[s]} if requires and s in requires else {}),
            }
            for s in skills
        ],
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo, f"file://{repo}"


def _commit(repo: Path, msg: str = "next") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _project(tmp_path: Path, sources: dict[str, str], comps: list[tuple[str, str]]) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    for name, url in sources.items():
        store.add_source(Source(name=name, type=SourceType.GIT, url=url, ref="main"))
    for src, skill in comps:
        store.add_component(Component(source=src, type=_C.SKILL, name=skill))
    store.save()
    return proj


def _entry(proj: Path, name: str) -> dict:
    e = load_lock(proj).entry(name)
    assert e is not None
    return e.model_dump(mode="json")


def _skill_text(proj: Path, name: str) -> str:
    return (proj / f".claude/skills/{name}/SKILL.md").read_text(encoding="utf-8")


def _trust(proj: Path, name: str) -> None:
    lock = load_lock(proj)
    lock.entry(name).trusted = True
    save_lock(proj, lock)


@pytest.fixture
def two_sources(tmp_path: Path):
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "v1"})
    repo_b, url_b = _repo(tmp_path, "b", {"rb": "v1"})
    proj = _project(tmp_path, {"a": url_a, "b": url_b}, [("a", "ra"), ("b", "rb")])
    sync(proj)
    return proj, repo_a, repo_b


def _advance(repo: Path, skill: str, body: str) -> str:
    _write_skill(repo, skill, body)
    return _commit(repo, body)


# --- only the named sources move -------------------------------------------------------


def test_update_named_source_leaves_other_entries_byte_identical(two_sources):
    proj, repo_a, repo_b = two_sources
    _trust(proj, "b")
    b_before = _entry(proj, "b")
    a_sha = _advance(repo_a, "ra", "v2")
    _advance(repo_b, "rb", "v2")

    sync(proj, update=True, update_only=frozenset({"a"}))

    assert load_lock(proj).entry("a").resolved == a_sha
    assert _entry(proj, "b") == b_before  # pin + trust untouched
    assert "v2" in _skill_text(proj, "ra")
    assert "v1" in _skill_text(proj, "rb")


def test_named_source_trust_follows_its_sha(two_sources):
    proj, repo_a, _ = two_sources
    _trust(proj, "a")
    sync(proj, update=True, update_only=frozenset({"a"}))  # upstream unchanged
    assert load_lock(proj).entry("a").trusted is True

    _advance(repo_a, "ra", "v2")
    sync(proj, update=True, update_only=frozenset({"a"}))
    assert load_lock(proj).entry("a").trusted is False  # SHA moved: re-consent needed


def test_bare_update_still_updates_every_source(two_sources):
    proj, repo_a, repo_b = two_sources
    a_sha = _advance(repo_a, "ra", "v2")
    b_sha = _advance(repo_b, "rb", "v2")
    sync(proj, update=True)
    assert load_lock(proj).entry("a").resolved == a_sha
    assert load_lock(proj).entry("b").resolved == b_sha


def test_cli_update_several_names(tmp_path: Path, monkeypatch):
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "v1"})
    repo_b, url_b = _repo(tmp_path, "b", {"rb": "v1"})
    repo_c, url_c = _repo(tmp_path, "c", {"rc": "v1"})
    proj = _project(
        tmp_path, {"a": url_a, "b": url_b, "c": url_c}, [("a", "ra"), ("b", "rb"), ("c", "rc")]
    )
    sync(proj)
    c_before = _entry(proj, "c")
    a_sha, b_sha = _advance(repo_a, "ra", "v2"), _advance(repo_b, "rb", "v2")
    _advance(repo_c, "rc", "v2")

    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["update", "a", "b"])
    assert result.exit_code == 0, result.output
    assert load_lock(proj).entry("a").resolved == a_sha
    assert load_lock(proj).entry("b").resolved == b_sha
    assert _entry(proj, "c") == c_before


def test_update_named_is_idempotent(two_sources):
    proj, repo_a, _ = two_sources
    _advance(repo_a, "ra", "v2")
    sync(proj, update=True, update_only=frozenset({"a"}))
    before = lock_path(proj).read_bytes()
    res = sync(proj, update=True, update_only=frozenset({"a"}))
    assert res.created == [] and res.updated == [] and res.removed == []
    assert lock_path(proj).read_bytes() == before


# --- unknown names fail before anything happens ----------------------------------------


def test_unknown_source_name_exits_1_with_suggestion_and_writes_nothing(two_sources, monkeypatch):
    proj, repo_a, _ = two_sources
    _advance(repo_a, "ra", "v2")
    lock_before = lock_path(proj).read_bytes()
    cfg_before = (proj / ".agentry.yml").read_bytes()

    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["update", "aa", "zz"])
    assert result.exit_code == 1
    assert (
        "unknown source 'aa' — did you mean 'a'? unknown source 'zz'. Nothing was changed. "
        "See `agentry source list`."
    ) in " ".join(result.stderr.split())
    assert lock_path(proj).read_bytes() == lock_before
    assert (proj / ".agentry.yml").read_bytes() == cfg_before
    assert "v1" in _skill_text(proj, "ra")


# --- generators --------------------------------------------------------------------------


def _generator(out: str) -> GeneratorSpec:
    script = (
        "import os;"
        f"os.makedirs({os.path.dirname(out)!r}, exist_ok=True);"
        f"open({out!r}, 'a').write('run\\n')"
    )
    return GeneratorSpec(command=[sys.executable, "-c", script], produces=[os.path.dirname(out)])


def test_only_named_sources_generators_rerun(tmp_path: Path):
    _, url_a = _repo(tmp_path, "a", {"ra": "v1"})
    _, url_b = _repo(tmp_path, "b", {"rb": "v1"})
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="a", type=SourceType.GIT, url=url_a, ref="main"))
    store.add_source(Source(name="b", type=SourceType.GIT, url=url_b, ref="main"))
    gen_a, gen_b = ".claude/skills/gena/SKILL.md", ".claude/skills/genb/SKILL.md"
    store.add_component(
        Component(source="a", type=_C.SKILL, name="gena", generate=_generator(gen_a))
    )
    store.add_component(
        Component(source="b", type=_C.SKILL, name="genb", generate=_generator(gen_b))
    )
    store.save()

    sync(proj, allow_run=True)
    assert (proj / gen_a).read_text() == "run\n" and (proj / gen_b).read_text() == "run\n"

    sync(proj, update=True, update_only=frozenset({"a"}), allow_run=True)
    assert (proj / gen_a).read_text() == "run\nrun\n"  # named: re-ran
    assert (proj / gen_b).read_text() == "run\n"  # unnamed: outputs present, skipped


# --- dependencies of a named source --------------------------------------------------------


def test_update_named_source_resolves_newly_required_dependency(tmp_path: Path):
    _, url_c = _repo(tmp_path, "c", {"rc": "dep"})
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "v1"})
    proj = _project(tmp_path, {"a": url_a}, [("a", "ra")])
    sync(proj)

    _descriptor(
        repo_a,
        [
            {
                "name": "ra",
                "path": "skills/ra",
                "requires": [{"type": "skill", "name": "rc", "url": url_c, "ref": "main"}],
            }
        ],
    )
    _commit(repo_a, "require rc")
    sync(proj, update=True, update_only=frozenset({"a"}))

    assert (proj / ".claude/skills/rc/SKILL.md").exists()
    synth = [e for e in load_lock(proj).sources if e.synthesized]
    assert len(synth) == 1 and synth[0].url == url_c


def test_update_named_source_keeps_dependency_pin_when_ref_unchanged(tmp_path: Path):
    repo_c, url_c = _repo(tmp_path, "c", {"rc": "v1"})
    req = {"ra": [{"type": "skill", "name": "rc", "url": url_c, "ref": "main"}]}
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "v1"}, requires=req)
    proj = _project(tmp_path, {"a": url_a}, [("a", "ra")])
    sync(proj)
    (dep,) = [e for e in load_lock(proj).sources if e.synthesized]
    dep_sha = dep.resolved

    _advance(repo_c, "rc", "v2")  # the dependency moves upstream...
    a_sha = _advance(repo_a, "ra", "v2")  # ...and so does the named source
    sync(proj, update=True, update_only=frozenset({"a"}))

    assert load_lock(proj).entry("a").resolved == a_sha
    (dep,) = [e for e in load_lock(proj).sources if e.synthesized]
    assert dep.resolved == dep_sha  # same required ref: pin kept
    assert "v1" in _skill_text(proj, "rc")


def test_update_named_source_bumps_dep_when_required_ref_changes(tmp_path: Path):
    repo_c, url_c = _repo(tmp_path, "c", {"rc": "v1"})
    _git(repo_c, "checkout", "-qb", "dev")
    dev_sha = _advance(repo_c, "rc", "dev")
    _git(repo_c, "checkout", "-q", "main")
    req = {"ra": [{"type": "skill", "name": "rc", "url": url_c, "ref": "main"}]}
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "v1"}, requires=req)
    proj = _project(tmp_path, {"a": url_a}, [("a", "ra")])
    sync(proj)

    _descriptor(
        repo_a,
        [
            {
                "name": "ra",
                "path": "skills/ra",
                "requires": [{"type": "skill", "name": "rc", "url": url_c, "ref": "dev"}],
            }
        ],
    )
    _commit(repo_a, "require rc@dev")
    sync(proj, update=True, update_only=frozenset({"a"}))

    (dep,) = [e for e in load_lock(proj).sources if e.synthesized]
    assert dep.ref == "dev" and dep.resolved == dev_sha
    assert "dev" in _skill_text(proj, "rc")


def test_named_source_needing_missing_component_from_pinned_source_fails(
    tmp_path: Path, monkeypatch
):
    """Rule (b): `a` now needs `b/skill/newthing`, which only b's *upstream* has. Updating
    `a` alone must fail with the fix, writing nothing; `update a b` then succeeds."""
    repo_b, url_b = _repo(tmp_path, "b", {"rb": "v1"})
    req = {"ra": [{"type": "skill", "name": "rb", "url": url_b, "ref": "main"}]}
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "v1"}, requires=req)
    proj = _project(tmp_path, {"a": url_a, "b": url_b}, [("a", "ra"), ("b", "rb")])
    sync(proj)

    _write_skill(repo_b, "newthing", "new")
    _descriptor(
        repo_b,
        [{"name": "rb", "path": "skills/rb"}, {"name": "newthing", "path": "skills/newthing"}],
    )
    _commit(repo_b, "add newthing")
    _descriptor(
        repo_a,
        [
            {
                "name": "ra",
                "path": "skills/ra",
                "requires": [{"type": "skill", "name": "newthing", "url": url_b, "ref": "main"}],
            }
        ],
    )
    _commit(repo_a, "require newthing")
    lock_before = lock_path(proj).read_bytes()

    with pytest.raises(DependencyError) as exc:
        sync(proj, update=True, update_only=frozenset({"a"}))
    assert str(exc.value) == (
        "a/skill/ra now requires b/skill/newthing, but the pinned source 'b' doesn't provide "
        "'newthing'. Nothing was changed. Update both: `agentry update a b`"
    )
    assert lock_path(proj).read_bytes() == lock_before
    assert not (proj / ".claude/skills/newthing").exists()

    monkeypatch.chdir(proj)
    cli = runner.invoke(app, ["update", "a"])
    assert cli.exit_code == 1
    assert lock_path(proj).read_bytes() == lock_before

    ok = runner.invoke(app, ["update", "a", "b"])
    assert ok.exit_code == 0, ok.output
    assert (proj / ".claude/skills/newthing/SKILL.md").exists()


# --- interaction with T7 ---------------------------------------------------------------


def test_url_change_still_repins_an_unnamed_source(two_sources, tmp_path: Path):
    """`update a` doesn't freeze b: an edited url on b is a new identity, so b re-pins."""
    proj, _, repo_b = two_sources
    fork = tmp_path / "fork-b"
    _git(tmp_path, "clone", "-q", str(repo_b), str(fork))
    fork_sha = _advance(fork, "rb", "fork")
    cfg = proj / ".agentry.yml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(f"file://{repo_b}", f"file://{fork}"),
        encoding="utf-8",
    )

    sync(proj, update=True, update_only=frozenset({"a"}))
    entry = load_lock(proj).entry("b")
    assert entry.url == f"file://{fork}" and entry.resolved == fork_sha
    assert "fork" in _skill_text(proj, "rb")


# --- a refused update leaves the installed content at the old revision --------------------


def test_scoped_update_refusal_restores_store_checkout(tmp_path: Path):
    """When `update a` is refused (rule b), the *linked* skill content must still be the old
    revision: the store clone of `a` is restored to its pin, not left at the new HEAD."""
    repo_b, url_b = _repo(tmp_path, "b", {"rb": "v1"})
    req = {"ra": [{"type": "skill", "name": "rb", "url": url_b, "ref": "main"}]}
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "OLD"}, requires=req)
    proj = _project(tmp_path, {"a": url_a, "b": url_b}, [("a", "ra"), ("b", "rb")])
    sync(proj)
    assert "OLD" in _skill_text(proj, "ra")

    _write_skill(repo_a, "ra", "NEW")
    _descriptor(
        repo_a,
        [
            {
                "name": "ra",
                "path": "skills/ra",
                "requires": [{"type": "skill", "name": "missing", "url": url_b, "ref": "main"}],
            }
        ],
    )
    _commit(repo_a, "new rev needs b/missing")

    locked = load_lock(proj).entry("a").resolved
    with pytest.raises(DependencyError):
        sync(proj, update=True, update_only=frozenset({"a"}))
    assert "OLD" in _skill_text(proj, "ra")
    assert _git(proj / ".agentry" / "a", "rev-parse", "HEAD") == locked  # store == lock


def test_unscoped_update_failure_leaves_earlier_sources_at_their_pins(tmp_path: Path):
    """A resolve failure partway through a bare `update` must not leave sources resolved
    before it checked out at their new HEAD: installed content stays at the old pins."""
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "OLD"})
    repo_b, url_b = _repo(tmp_path, "b", {"rb": "v1"})
    proj = _project(tmp_path, {"a": url_a, "b": url_b}, [("a", "ra"), ("b", "rb")])
    sync(proj)
    lock_before = lock_path(proj).read_bytes()

    _advance(repo_a, "ra", "NEW")
    repo_b.rename(tmp_path / "gone")  # b's origin disappears: fetching b fails

    with pytest.raises(Exception):  # noqa: B017 - any resolve failure
        sync(proj, update=True)
    assert lock_path(proj).read_bytes() == lock_before
    assert "OLD" in _skill_text(proj, "ra")


def test_update_interrupt_restores_store_checkout(tmp_path, monkeypatch):
    """Ctrl-C after one source has moved: the moved store checkout goes back to its lock SHA
    and the KeyboardInterrupt still propagates."""
    repo_a, url_a = _repo(tmp_path, "a", {"ra": "OLD"})
    _, url_b = _repo(tmp_path, "b", {"rb": "v1"})
    proj = _project(tmp_path, {"a": url_a, "b": url_b}, [("a", "ra"), ("b", "rb")])
    sync(proj)
    locked = load_lock(proj).entry("a").resolved
    _advance(repo_a, "ra", "NEW")

    import agentry.deps as deps_mod

    real = deps_mod.resolve

    def resolve_then_interrupt(root, src, **kw):
        if src.name == "b":
            raise KeyboardInterrupt
        return real(root, src, **kw)

    monkeypatch.setattr(deps_mod, "resolve", resolve_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        sync(proj, update=True)
    monkeypatch.setattr(deps_mod, "resolve", real)

    assert _git(proj / ".agentry" / "a", "rev-parse", "HEAD") == locked
    assert "OLD" in _skill_text(proj, "ra")


def test_scoped_update_interrupt_during_walk_restores_store_checkout(tmp_path, monkeypatch):
    """Ctrl-C inside the dependency walk of `update a` (after a's checkout moved): the store
    goes back to the lock SHA, the installed skill is the old revision, and the interrupt
    propagates."""
    from agentry import discovery

    repo_a, url_a = _repo(tmp_path, "a", {"ra": "OLD"})
    _, url_b = _repo(tmp_path, "b", {"rb": "v1"})
    proj = _project(tmp_path, {"a": url_a, "b": url_b}, [("a", "ra"), ("b", "rb")])
    sync(proj)
    locked = load_lock(proj).entry("a").resolved
    _advance(repo_a, "ra", "NEW")

    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(discovery, "requires_for", interrupt)
    with pytest.raises(KeyboardInterrupt):
        sync(proj, update=True, update_only=frozenset({"a"}))
    monkeypatch.undo()

    assert _git(proj / ".agentry" / "a", "rev-parse", "HEAD") == locked
    assert "OLD" in _skill_text(proj, "ra")
