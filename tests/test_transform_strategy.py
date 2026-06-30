"""Tests for the per-component transform strategy (copy-with-rewrite)."""

from __future__ import annotations

from pathlib import Path

from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import sync

_C = ComponentType


def _agent_project(
    tmp_path: Path, *, provider: str, prompt: str | None = None, command: list[str] | None = None
) -> Path:
    src = tmp_path / "src"
    (src / "agents").mkdir(parents=True)
    (src / "agents" / "a.md").write_text("---\nname: a\nmodel: x\n---\nBe helpful.\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.AGENT, name="a"))
    store.doc["components"][0]["transform"] = {"provider": provider}
    if prompt is not None:
        store.doc["components"][0]["transform"]["prompt"] = prompt
    if command is not None:
        store.doc["transform"] = {"command": command}
    store.save()
    return proj


def test_strip_frontmatter_installs_a_real_rewritten_file(tmp_path: Path):
    proj = _agent_project(tmp_path, provider="strip-frontmatter")
    sync(proj)
    dest = proj / ".claude/agents/a.md"
    assert dest.is_file() and not dest.is_symlink()  # a committed copy, not a live symlink
    assert dest.read_text() == "Be helpful.\n"  # frontmatter dropped
    res = sync(proj)  # deterministic → idempotent
    assert res.created == [] and res.updated == []


def test_transform_output_is_reversible(tmp_path: Path):
    proj = _agent_project(tmp_path, provider="strip-frontmatter")
    sync(proj)
    dest = proj / ".claude/agents/a.md"
    assert dest.exists()
    store = ConfigStore.load(proj)
    store.set_enabled("s/agent/a", False)
    store.save()
    sync(proj)
    assert not dest.exists()


def test_transform_never_clobbers_a_hand_authored_file(tmp_path: Path):
    proj = _agent_project(tmp_path, provider="strip-frontmatter")
    (proj / ".claude/agents").mkdir(parents=True)
    (proj / ".claude/agents/a.md").write_text("HAND-WRITTEN\n")
    res = sync(proj)
    assert (proj / ".claude/agents/a.md").read_text() == "HAND-WRITTEN\n"  # untouched
    assert any("isn't managed" in w for w in res.warnings)


def test_agent_transform_requires_allow_transform(tmp_path: Path):
    proj = _agent_project(tmp_path, provider="agent", command=["fake-agent"])
    res = sync(proj)  # allow_transform defaults False
    assert not (proj / ".claude/agents/a.md").exists()
    assert any("allow-transform" in w for w in res.warnings)


def test_agent_transform_synthesizes_and_is_write_once(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    def fake_run_agent(command, prompt):
        calls.append(prompt)
        return "SYNTHESIZED\n"

    # transform.render binds run_agent from emit at import; patch it where it's used.
    monkeypatch.setattr("agentry.installers.transform.run_agent", fake_run_agent)
    proj = _agent_project(tmp_path, provider="agent", prompt="Make it portable.", command=["x"])
    sync(proj, allow_transform=True)
    dest = proj / ".claude/agents/a.md"
    assert dest.read_text() == "SYNTHESIZED\n" and not dest.is_symlink()
    assert len(calls) == 1 and "Make it portable." in calls[0]
    # Write-once: a second sync does NOT re-invoke the (non-reproducible) agent.
    sync(proj, allow_transform=True)
    assert len(calls) == 1


def test_transform_unsupported_for_dir_type_installs_normally(tmp_path: Path):
    src = tmp_path / "src"
    (src / "skills" / "sk").mkdir(parents=True)
    (src / "skills" / "sk" / "SKILL.md").write_text("# sk\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.SKILL, name="sk"))
    store.doc["components"][0]["transform"] = {"provider": "strip-frontmatter"}
    store.save()
    res = sync(proj)
    assert (proj / ".claude/skills/sk").is_symlink()  # fell back to a normal symlink
    assert any("only supported for file components" in w for w in res.warnings)
