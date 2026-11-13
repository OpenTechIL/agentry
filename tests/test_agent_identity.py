"""ADR-003: Claude identifies agents by frontmatter `name`, not by path.

Two agents declaring the same `name` collide even at different paths (flat or namespaced): the
earliest in claim order wins (W5-name). A user's own agent with the same name always beats
ours (W1-name) and is never touched. The identity rule is data on the claude driver only, and
profiles can set or clear it. Hermetic: local sources under tmp_path; HOME is a sentinel.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.manifest import load_manifest
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync

_C = ComponentType
_NS_PROFILE = {"claude": {"agent": {"strategy": "link", "dest": ".claude/agents/ns/{name}.md"}}}


def _agent(name: str | None, body: str = "body") -> str:
    head = (
        f"---\nname: {name}\ndescription: d\n---\n"
        if name is not None
        else "---\ndescription: d\n---\n"
    )
    return head + body + "\n"


def _src(tmp_path: Path, label: str, agents: dict[str, str]) -> Path:
    root = tmp_path / f"src-{label}"
    for stem, text in agents.items():
        p = root / "agents" / f"{stem}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _project(tmp_path: Path, sources: dict[str, Path], comps, targets=("claude",), profiles=None):
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    store = ConfigStore.create(proj, list(targets))
    for name, root in sources.items():
        store.add_source(Source(name=name, type=SourceType.LOCAL, path=str(root)))
    for src, stem in comps:
        store.add_component(Component(source=src, type=_C.AGENT, name=stem))
    if profiles:
        store.doc["target_profiles"] = profiles
    store.save()
    return proj


def _w5_name(loser: str, winner: str, name: str, target: str = "claude") -> str:
    """W5-name, both components declared (backend's ADR-003 text)."""
    return (
        f"{loser}: agent name '{name}' on {target} is already used by {winner}, listed earlier "
        f"in .agentry.yml — skipped ({target} loads only one agent per name). Disable one of "
        "them, or reorder components to choose which wins."
    )


def _w1_name(ref: str, name: str, relpath: str, target: str = "claude") -> str:
    return (
        f"{ref}: agent name '{name}' on {target} is already used by your agent {relpath} — "
        f"skipped ({target} loads only one agent per name). Rename one of them, or remove yours "
        f"to install {ref}'s."
    )


def _row(proj: Path, ref: str):
    (row,) = [r for r in status(proj)[0] if r.ref == ref]
    return row


# --- name units ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["flat", "namespaced"])
def test_two_agents_same_frontmatter_name_first_in_config_wins(tmp_path: Path, layout: str):
    one = _src(tmp_path, "one", {"a": _agent("reviewer", "from one")})
    two = _src(tmp_path, "two", {"b": _agent("reviewer", "from two")})
    proj = _project(
        tmp_path,
        {"one": one, "two": two},
        [("one", "a"), ("two", "b")],
        profiles=_NS_PROFILE if layout == "namespaced" else None,
    )
    res = sync(proj)
    base = proj / (".claude/agents/ns" if layout == "namespaced" else ".claude/agents")
    assert (base / "a.md").is_symlink()  # the paths differ...
    assert not (base / "b.md").exists()  # ...but the names collide: b is skipped
    assert res.warnings.count(_w5_name("two/agent/b", "one/agent/a", "reviewer")) == 1
    row = _row(proj, "two/agent/b")
    assert row.state == "missing"
    assert (
        row.note == "agent name 'reviewer' is used by one/agent/a (listed earlier in .agentry.yml)"
    )


def test_agent_name_collision_casefolded(tmp_path: Path):
    one = _src(tmp_path, "one", {"a": _agent("Reviewer")})
    two = _src(tmp_path, "two", {"b": _agent("reviewer")})
    proj = _project(tmp_path, {"one": one, "two": two}, [("one", "a"), ("two", "b")])
    res = sync(proj)
    assert not (proj / ".claude/agents/b.md").exists()
    assert _w5_name("two/agent/b", "one/agent/a", "reviewer") in res.warnings  # loser's spelling


def test_agent_missing_name_falls_back_to_stem_with_warning(tmp_path: Path):
    """No `name`: the file stem is the identity (and it warns, since Claude requires name)."""
    one = _src(tmp_path, "one", {"helper": _agent(None)})
    two = _src(tmp_path, "two", {"other": _agent("helper")})
    proj = _project(tmp_path, {"one": one, "two": two}, [("one", "helper"), ("two", "other")])
    res = sync(proj)
    assert (
        "one/agent/helper: agent has no 'name' in its frontmatter, which claude uses to identify "
        "agents — treated it as 'helper' (its file name). Add 'name: helper' to the agent."
    ) in res.warnings
    assert (proj / ".claude/agents/helper.md").is_symlink()
    assert not (proj / ".claude/agents/other.md").exists()  # stem "helper" claimed first
    assert _w5_name("two/agent/other", "one/agent/helper", "helper") in res.warnings


def test_agent_name_with_colon_warns(tmp_path: Path):
    one = _src(tmp_path, "one", {"a": _agent("plug:reviewer")})
    proj = _project(tmp_path, {"one": one}, [("one", "a")])
    res = sync(proj)
    assert (
        "one/agent/a: agent name 'plug:reviewer' contains ':', which claude reserves for plugin "
        "agents — installed as-is. Rename it in the agent's frontmatter."
    ) in res.warnings
    assert (proj / ".claude/agents/a.md").is_symlink()  # a warning, not a refusal


# --- the user's own agents ------------------------------------------------------------------


def _user_agent(proj: Path, rel: str, name: str) -> Path:
    path = proj / ".claude" / "agents" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_agent(name, "the user's own"), encoding="utf-8")
    return path


def test_user_agent_same_name_refuses_ours_w1_name(tmp_path: Path):
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    proj = _project(tmp_path, {"one": one}, [("one", "a")])
    mine = _user_agent(proj, "mine.md", "reviewer")
    before = mine.read_bytes()

    res = sync(proj)
    assert not (proj / ".claude/agents/a.md").exists()  # ours refused
    assert mine.read_bytes() == before  # the user's agent untouched
    assert _w1_name("one/agent/a", "reviewer", ".claude/agents/mine.md") in res.warnings
    row = _row(proj, "one/agent/a")
    assert row.state == "missing"
    assert row.note == "agent name 'reviewer' is used by your agent .claude/agents/mine.md"
    sync(proj)
    assert mine.read_bytes() == before  # still untouched on the next sync


def test_user_agent_scan_reads_only_never_writes(tmp_path: Path):
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    proj = _project(tmp_path, {"one": one}, [("one", "a")])
    files = [_user_agent(proj, "x.md", "x"), _user_agent(proj, "deep/nested/y.md", "reviewer")]
    for f in files:
        os.utime(f, ns=(1_000_000_000, 1_000_000_000))
    snap = {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in files}

    sync(proj)
    assert {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in files} == snap
    assert not (proj / ".claude/agents/a.md").exists()  # found recursively, so ours refused
    assert _w1_name("one/agent/a", "reviewer", ".claude/agents/deep/nested/y.md") in (
        sync(proj).warnings
    )


# --- scope and upgrade --------------------------------------------------------------------


def test_identity_rule_only_on_claude_by_default(tmp_path: Path):
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    two = _src(tmp_path, "two", {"b": _agent("reviewer")})
    proj = _project(
        tmp_path,
        {"one": one, "two": two},
        [("one", "a"), ("two", "b")],
        targets=("claude", "opencode"),
    )
    res = sync(proj)
    assert (proj / ".opencode/agents/a.md").exists() and (proj / ".opencode/agents/b.md").exists()
    assert not (proj / ".claude/agents/b.md").exists()  # claude still applies the rule
    assert not any("on opencode" in w for w in res.warnings)
    assert _w5_name("two/agent/b", "one/agent/a", "reviewer") in res.warnings


def test_profile_can_set_and_clear_identity_rule(tmp_path: Path):
    """A profile may set the rule (opencode) or clear it (claude); a placement-only override
    inherits it."""
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    two = _src(tmp_path, "two", {"b": _agent("reviewer")})
    sources, comps = {"one": one, "two": two}, [("one", "a"), ("two", "b")]

    setp = _project(
        tmp_path / "set",
        sources,
        comps,
        targets=("opencode",),
        profiles={
            "opencode": {
                "agent": {
                    "strategy": "link",
                    "dest": ".opencode/agents/{name}.md",
                    "identity": "frontmatter:name",
                }
            }
        },
    )
    res = sync(setp)
    assert not (setp / ".opencode/agents/b.md").exists()  # rule set: collides
    assert _w5_name("two/agent/b", "one/agent/a", "reviewer", "opencode") in res.warnings

    clearp = _project(
        tmp_path / "clear",
        sources,
        comps,
        profiles={
            "claude": {
                "agent": {"strategy": "link", "dest": ".claude/agents/{name}.md", "identity": None}
            }
        },
    )
    res = sync(clearp)
    assert (clearp / ".claude/agents/b.md").exists()  # rule cleared: both install
    assert not any("agent name" in w for w in res.warnings)

    inherit = _project(tmp_path / "inherit", sources, comps, profiles=_NS_PROFILE)
    sync(inherit)
    assert not (inherit / ".claude/agents/ns/b.md").exists()  # placement-only: still applies


def test_existing_duplicate_name_installs_resolve_on_upgrade(tmp_path: Path):
    """`two`'s agent is installed (what Claude's random pick left); `one`, listed first, ships
    the same name: the next sync keeps one, removes two's, and reports the transfer once."""
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    two = _src(tmp_path, "two", {"b": _agent("reviewer")})
    proj = _project(tmp_path, {"one": one, "two": two}, [("two", "b")])
    sync(proj)
    assert (proj / ".claude/agents/b.md").is_symlink()
    store = ConfigStore.load(proj)
    store.doc["components"].insert(
        0,
        Component(source="one", type=_C.AGENT, name="a").model_dump(mode="json", exclude_none=True),
    )
    store.save()

    res = sync(proj)
    assert (proj / ".claude/agents/a.md").is_symlink()
    assert not (proj / ".claude/agents/b.md").exists()
    transfer = (
        "agent name 'reviewer' on claude now comes from one/agent/a (listed earlier in "
        ".agentry.yml) instead of two/agent/b — to keep two/agent/b's version, list it before "
        "one/agent/a in .agentry.yml, or disable one/agent/a"
    )
    assert res.updated.count(transfer) == 1
    assert _w5_name("two/agent/b", "one/agent/a", "reviewer") in res.warnings
    assert not [u for u in sync(proj).updated if "now comes from" in u]  # once only


def test_namespaced_agent_paths_unchanged(tmp_path: Path):
    """Guard: namespaced agent installs keep their paths — no churn."""
    one = _src(tmp_path, "one", {"a": _agent("alpha")})
    proj = _project(tmp_path, {"one": one}, [("one", "a")], profiles=_NS_PROFILE)
    sync(proj)
    assert (proj / ".claude/agents/ns/a.md").is_symlink()
    assert [link.path for link in load_manifest(proj).links] == [".claude/agents/ns/a.md"]


def test_agent_name_claims_idempotent(tmp_path: Path):
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    two = _src(tmp_path, "two", {"b": _agent("reviewer")})
    proj = _project(tmp_path, {"one": one, "two": two}, [("one", "a"), ("two", "b")])
    first = sync(proj)
    second = sync(proj)
    assert second.created == [] and second.updated == [] and second.removed == []
    assert sorted(second.warnings) == sorted(first.warnings)
    assert _w5_name("two/agent/b", "one/agent/a", "reviewer") in second.warnings
    assert (proj / ".claude/agents/a.md").is_symlink() and not (
        proj / ".claude/agents/b.md"
    ).exists()


# --- PM's required additions --------------------------------------------------------------


def test_hermetic_never_reads_home_claude_agents(tmp_path: Path, monkeypatch):
    """The W1-name scan reads only the project's agent dir. A same-name agent in
    ~/.claude/agents must not refuse ours (project agents take precedence upstream)."""
    home = tmp_path / "home-sentinel"
    sentinel = home / ".claude" / "agents" / "home.md"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text(_agent("reviewer", "home"), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    proj = _project(tmp_path, {"one": one}, [("one", "a")])
    res = sync(proj)
    assert (proj / ".claude/agents/a.md").is_symlink()  # not refused by the HOME agent
    assert not any("home.md" in w or str(home) in w for w in res.warnings)


def test_malformed_user_agent_frontmatter_skipped(tmp_path: Path, monkeypatch):
    """Malformed or unreadable user agent files are skipped silently — no crash, and they
    don't block ours."""
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    proj = _project(tmp_path, {"one": one}, [("one", "a")])
    agents = proj / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "broken.md").write_text("---\nname: [unclosed\n", encoding="utf-8")
    (agents / "binary.md").write_bytes(b"\xff\xfe\x00---\nname: \x00\n")

    res = sync(proj)  # must not raise
    assert (proj / ".claude/agents/a.md").is_symlink()
    assert not any("broken.md" in w or "binary.md" in w for w in res.warnings)
    monkeypatch.chdir(proj)
    assert CliRunner().invoke(app, ["doctor"]).exit_code in (0, 1)  # doctor doesn't crash either


def test_symlinked_user_agent_not_scanned(tmp_path: Path):
    """The W1-name scan doesn't follow symlinks: a hand-made link in .claude/agents to a
    same-name agent elsewhere does not refuse ours, and is left as it is."""
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    proj = _project(tmp_path, {"one": one}, [("one", "a")])
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text(_agent("reviewer", "linked"), encoding="utf-8")
    link = proj / ".claude" / "agents" / "linked.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(elsewhere)

    res = sync(proj)
    assert (proj / ".claude/agents/a.md").is_symlink()
    assert link.is_symlink() and os.readlink(link) == str(elsewhere)
    assert not any("linked.md" in w for w in res.warnings)


def test_user_agent_name_refuses_two_of_ours_w1_each_no_w5(tmp_path: Path):
    """The user's agent beats every one of ours with that name: each gets W1-name, and ours
    never collide with each other (W1 claims the name first)."""
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    two = _src(tmp_path, "two", {"b": _agent("Reviewer")})
    proj = _project(tmp_path, {"one": one, "two": two}, [("one", "a"), ("two", "b")])
    mine = _user_agent(proj, "mine.md", "reviewer")
    before = mine.read_bytes()

    res = sync(proj)
    assert not (proj / ".claude/agents/a.md").exists()
    assert not (proj / ".claude/agents/b.md").exists()
    assert _w1_name("one/agent/a", "reviewer", ".claude/agents/mine.md") in res.warnings
    assert _w1_name("two/agent/b", "Reviewer", ".claude/agents/mine.md") in res.warnings
    assert not any("is already used by one/agent/a" in w for w in res.warnings)  # no W5-name
    assert mine.read_bytes() == before


def test_name_collision_on_two_targets_one_line_per_target(tmp_path: Path):
    """With the rule on claude and (via profile) opencode, the loser gets one W5-name line per
    target, each exactly once."""
    one = _src(tmp_path, "one", {"a": _agent("reviewer")})
    two = _src(tmp_path, "two", {"b": _agent("reviewer")})
    proj = _project(
        tmp_path,
        {"one": one, "two": two},
        [("one", "a"), ("two", "b")],
        targets=("claude", "opencode"),
        profiles={
            "opencode": {
                "agent": {
                    "strategy": "link",
                    "dest": ".opencode/agents/{name}.md",
                    "identity": "frontmatter:name",
                }
            }
        },
    )
    res = sync(proj)
    for target in ("claude", "opencode"):
        assert res.warnings.count(_w5_name("two/agent/b", "one/agent/a", "reviewer", target)) == 1


def test_copy_strategy_agents_use_name_units(tmp_path: Path):
    one = _src(tmp_path, "one", {"a": _agent("reviewer", "from one")})
    two = _src(tmp_path, "two", {"b": _agent("reviewer", "from two")})
    proj = _project(
        tmp_path,
        {"one": one, "two": two},
        [("one", "a"), ("two", "b")],
        profiles={"claude": {"agent": {"strategy": "copy", "dest": ".claude/agents/{name}.md"}}},
    )
    res = sync(proj)
    a = proj / ".claude/agents/a.md"
    assert a.is_file() and not a.is_symlink() and "from one" in a.read_text("utf-8")
    assert not (proj / ".claude/agents/b.md").exists()
    assert _w5_name("two/agent/b", "one/agent/a", "reviewer") in res.warnings
    assert sync(proj).created == []  # idempotent


_MISSING_NAME = (
    "one/agent/helper: agent has no 'name' in its frontmatter, which claude uses to identify "
    "agents — treated it as 'helper' (its file name). Add 'name: helper' to the agent."
)
_COLON_NAME = (
    "one/agent/a: agent name 'plug:reviewer' contains ':', which claude reserves for plugin "
    "agents — installed as-is. Rename it in the agent's frontmatter."
)


def test_doctor_lists_agent_name_warnings_and_strict_fails(tmp_path: Path, monkeypatch):
    from agentry.doctor import run_checks

    one = _src(tmp_path, "one", {"helper": _agent(None), "a": _agent("plug:reviewer")})
    proj = _project(tmp_path, {"one": one}, [("one", "helper"), ("one", "a")])
    sync(proj)

    warns = [(c.category, c.message) for c in run_checks(proj) if c.level == "warn"]
    assert warns.count(("component", _MISSING_NAME)) == 1
    assert warns.count(("component", _COLON_NAME)) == 1

    # PATH without the venv: doctor's `agy` alias check would otherwise warn on its own and
    # make `--strict` fail regardless of the agent-name warnings.
    env = {"COLUMNS": "400", "PATH": "/usr/bin:/bin"}
    control = _project(tmp_path / "control", {"one": one}, [])
    monkeypatch.chdir(control)
    assert CliRunner(env=env).invoke(app, ["doctor", "--strict"]).exit_code == 0  # baseline

    monkeypatch.chdir(proj)
    plain = CliRunner(env=env).invoke(app, ["doctor"])
    assert plain.exit_code == 0, plain.output  # warnings only
    out = " ".join(plain.output.split())
    assert _MISSING_NAME in out and _COLON_NAME in out
    assert CliRunner(env=env).invoke(app, ["doctor", "--strict"]).exit_code == 1
