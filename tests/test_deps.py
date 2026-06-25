from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.deps import DependencyError, resolve_graph
from agentry.lockfile import load_lock
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import sync

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.x",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.x",
}


def _skill(repo: Path, name: str, body: str = "skill\n") -> None:
    d = repo / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {name}\n{body}")


def _descriptor(repo: Path, provides: dict) -> None:
    (repo / "agentry.yaml").write_text(json.dumps({"version": 1, "provides": provides}))


def _git_init(repo: Path, ref: str = "main") -> str:
    subprocess.run(["git", "init", "-q", "-b", ref], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=_ENV)
    return f"file://{repo}"


def test_same_source_dependency(tmp_path: Path):
    """A skill requiring a sibling skill in the same repo pulls the sibling in."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _skill(repo, "a")
    _skill(repo, "b")
    _descriptor(repo, {"skill": [
        {"name": "a", "path": "skills/a", "requires": [{"type": "skill", "name": "b"}]},
        {"name": "b", "path": "skills/b"},
    ]})
    url = _git_init(repo)

    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="g", type=SourceType.GIT, url=url, ref="main"))
    store.add_component(Component(source="g", type=ComponentType.SKILL, name="a"))
    store.save()

    sync(proj)
    assert (proj / ".claude/skills/a/SKILL.md").exists()
    assert (proj / ".claude/skills/b/SKILL.md").exists()  # transitive sibling installed
    # b was NOT written into .agentry.yml — it's a dependency.
    assert "name: b" not in (proj / ".agentry.yml").read_text()


def test_cross_repo_transitive_lock_only(tmp_path: Path):
    """A url dependency on another repo is resolved into the lock, not .agentry.yml."""
    libb = tmp_path / "libb"
    libb.mkdir()
    _skill(libb, "b")
    _descriptor(libb, {"skill": [{"name": "b", "path": "skills/b"}]})
    url_b = _git_init(libb)

    liba = tmp_path / "liba"
    liba.mkdir()
    _skill(liba, "a")
    _descriptor(liba, {"skill": [
        {"name": "a", "path": "skills/a",
         "requires": [{"type": "skill", "name": "b", "url": url_b, "ref": "main"}]},
    ]})
    url_a = _git_init(liba)

    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="a", type=SourceType.GIT, url=url_a, ref="main"))
    store.add_component(Component(source="a", type=ComponentType.SKILL, name="a"))
    store.save()

    sync(proj)
    assert (proj / ".claude/skills/a/SKILL.md").exists()
    assert (proj / ".claude/skills/b/SKILL.md").exists()

    lock = load_lock(proj)
    synth = [e for e in lock.sources if e.synthesized]
    assert len(synth) == 1 and len(synth[0].resolved) == 40
    # The transitive source is in the lock but not in the committed config.
    cfg = (proj / ".agentry.yml").read_text()
    assert url_b not in cfg


def test_recursive_three_levels(tmp_path: Path):
    """A -> B -> C across three repos resolves the full chain."""
    libc = tmp_path / "libc"
    libc.mkdir()
    _skill(libc, "c")
    _descriptor(libc, {"skill": [{"name": "c", "path": "skills/c"}]})
    url_c = _git_init(libc)

    libb = tmp_path / "libb"
    libb.mkdir()
    _skill(libb, "b")
    _descriptor(libb, {"skill": [
        {"name": "b", "path": "skills/b",
         "requires": [{"type": "skill", "name": "c", "url": url_c}]},
    ]})
    url_b = _git_init(libb)

    liba = tmp_path / "liba"
    liba.mkdir()
    _skill(liba, "a")
    _descriptor(liba, {"skill": [
        {"name": "a", "path": "skills/a",
         "requires": [{"type": "skill", "name": "b", "url": url_b}]},
    ]})
    url_a = _git_init(liba)

    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="a", type=SourceType.GIT, url=url_a, ref="main"))
    store.add_component(Component(source="a", type=ComponentType.SKILL, name="a"))
    store.save()

    sync(proj)
    for name in ("a", "b", "c"):
        assert (proj / f".claude/skills/{name}/SKILL.md").exists()


def test_cycle_terminates(tmp_path: Path):
    """A <-> B mutual dependency resolves without infinite recursion."""
    # Build B first referencing A's url, then A referencing B's url. The urls are
    # deterministic file:// paths, so we can write them before git init.
    liba = tmp_path / "liba"
    libb = tmp_path / "libb"
    liba.mkdir()
    libb.mkdir()
    url_a = f"file://{liba}"
    url_b = f"file://{libb}"

    _skill(liba, "a")
    _descriptor(liba, {"skill": [
        {"name": "a", "path": "skills/a", "requires": [{"type": "skill", "name": "b", "url": url_b}]},
    ]})
    _skill(libb, "b")
    _descriptor(libb, {"skill": [
        {"name": "b", "path": "skills/b", "requires": [{"type": "skill", "name": "a", "url": url_a}]},
    ]})
    _git_init(liba)
    _git_init(libb)

    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="a", type=SourceType.GIT, url=url_a, ref="main"))
    store.add_component(Component(source="a", type=ComponentType.SKILL, name="a"))
    store.save()

    sync(proj)  # must terminate
    assert (proj / ".claude/skills/a/SKILL.md").exists()
    assert (proj / ".claude/skills/b/SKILL.md").exists()


def test_version_conflict_raises(tmp_path: Path):
    """Two requirers pinning the same repo to different refs aborts with a clear error."""
    # libb has two refs: main and a tag v2.
    libb = tmp_path / "libb"
    libb.mkdir()
    _skill(libb, "b")
    _descriptor(libb, {"skill": [{"name": "b", "path": "skills/b"}]})
    url_b = _git_init(libb)
    subprocess.run(["git", "tag", "v2"], cwd=libb, check=True, env=_ENV)

    # liba requires b@main; libx requires b@v2. Both are roots in one project.
    liba = tmp_path / "liba"
    liba.mkdir()
    _skill(liba, "a")
    _descriptor(liba, {"skill": [
        {"name": "a", "path": "skills/a",
         "requires": [{"type": "skill", "name": "b", "url": url_b, "ref": "main"}]},
    ]})
    url_a = _git_init(liba)

    libx = tmp_path / "libx"
    libx.mkdir()
    _skill(libx, "x")
    _descriptor(libx, {"skill": [
        {"name": "x", "path": "skills/x",
         "requires": [{"type": "skill", "name": "b", "url": url_b, "ref": "v2"}]},
    ]})
    url_x = _git_init(libx)

    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="a", type=SourceType.GIT, url=url_a, ref="main"))
    store.add_source(Source(name="x", type=SourceType.GIT, url=url_x, ref="main"))
    store.add_component(Component(source="a", type=ComponentType.SKILL, name="a"))
    store.add_component(Component(source="x", type=ComponentType.SKILL, name="x"))
    store.save()

    with pytest.raises(DependencyError) as exc:
        sync(proj)
    assert "version conflict" in str(exc.value)


def test_graph_edges_and_transitive(tmp_path: Path):
    """resolve_graph exposes edges and marks transitive refs for the dependency map."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _skill(repo, "a")
    _skill(repo, "b")
    _descriptor(repo, {"skill": [
        {"name": "a", "path": "skills/a", "requires": [{"type": "skill", "name": "b"}]},
        {"name": "b", "path": "skills/b"},
    ]})
    url = _git_init(repo)

    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="g", type=SourceType.GIT, url=url, ref="main"))
    store.add_component(Component(source="g", type=ComponentType.SKILL, name="a"))
    store.save()

    config = store.parsed()
    graph, _ = resolve_graph(proj, config, load_lock(proj))
    assert any(e.dependent == "g/skill/a" and e.dependency == "g/skill/b" for e in graph.edges)
    assert "g/skill/b" in graph.transitive
    assert "g/skill/a" not in graph.transitive  # a is a declared root
