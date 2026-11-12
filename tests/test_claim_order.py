"""T22 / ADR-001 Rev 6: duplicate claims resolve in `.agentry.yml` order.

When two components claim the same named merge key or the same destination path, the one
listed first wins and every later claimant gets W5 — reproducibly, independent of install
history. A reorder transfers ownership in one sync (fingerprint-safe: user edits are never
overwritten). Array-mode hook elements are shared, never W5.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.lockfile import lock_path
from agentry.manifest import load_manifest
from agentry.models import Component, ComponentTransform, ComponentType, Source, SourceType
from agentry.reconcile import status, sync

_C = ComponentType
runner = CliRunner()
REVIEW = ".claude/commands/review.md"


def _w5_key(ref: str, key: str, other: str, pointer="mcpServers", file=".mcp.json") -> str:
    return (
        f"{ref}: '{pointer}.{key}' in {file} is already provided by {other}, listed earlier in "
        ".agentry.yml — skipped. Disable one of them, rename the entry, or reorder components "
        "to choose which wins."
    )


def _w5_path(ref: str, path: str, other: str, target: str = "claude") -> str:
    return (
        f"{ref}: {path} is already installed by {other}, listed earlier in .agentry.yml — "
        f"skipped on {target}. Disable one of them, or reorder components to choose which wins."
    )


def _transfer(unit: str, winner: str, loser: str) -> str:
    return (
        f"{unit} now comes from {winner} (listed earlier in .agentry.yml) instead of {loser} — "
        f"to keep {loser}'s version, list it before {winner} in .agentry.yml, or disable {winner}"
    )


def _sources(tmp_path: Path) -> dict[str, Path]:
    """Two local sources that collide on commands/review.md, mcp key 'srv', and hook keys."""
    out = {}
    for name in ("one", "two"):
        root = tmp_path / f"src-{name}"
        (root / "commands").mkdir(parents=True)
        (root / "commands" / "review.md").write_text(f"# review from {name}\n", "utf-8")
        (root / "mcp").mkdir()
        (root / "mcp" / "srv.json").write_text(
            json.dumps({"srv": {"command": f"from-{name}"}}), "utf-8"
        )
        out[name] = root
    return out


def _project(tmp_path: Path, comps: list[Component], profiles: dict | None = None) -> Path:
    srcs = _sources(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    store = ConfigStore.create(proj, ["claude"])
    for name, root in srcs.items():
        store.add_source(Source(name=name, type=SourceType.LOCAL, path=str(root)))
    for c in comps:
        store.add_component(c)
    if profiles:
        store.doc["target_profiles"] = profiles
    store.save()
    return proj


def _cmd(src: str, **kw) -> Component:
    return Component(source=src, type=_C.COMMAND, name="review", **kw)


def _mcp(src: str) -> Component:
    return Component(source=src, type=_C.MCP, name="srv")


def _reorder(proj: Path, *refs: str) -> None:
    store = ConfigStore.load(proj)
    comps = store.doc["components"]
    by_ref = {f"{c['source']}/{c['type']}/{c['name']}": c for c in comps}
    comps[:] = [by_ref[r] for r in refs] + [c for c in comps if c not in [by_ref[r] for r in refs]]
    store.save()


def _add_first(proj: Path, comp: Component) -> None:
    store = ConfigStore.load(proj)
    store.doc["components"].insert(0, comp.model_dump(mode="json", exclude_none=True))
    store.save()


def _srv(proj: Path) -> dict | None:
    path = proj / ".mcp.json"
    return (
        json.loads(path.read_text("utf-8")).get("mcpServers", {}).get("srv")
        if path.is_file()
        else None
    )


def _review(proj: Path) -> str:
    return (proj / REVIEW).read_text(encoding="utf-8")


# --- merges -----------------------------------------------------------------------------


@pytest.mark.parametrize("first", ["one", "two"])
def test_duplicate_named_key_first_in_config_wins(tmp_path: Path, first: str):
    second = "two" if first == "one" else "one"
    proj = _project(tmp_path, [_mcp(first), _mcp(second)])
    res = sync(proj)
    assert _srv(proj) == {"command": f"from-{first}"}
    assert _w5_key(f"{second}/mcp/srv", "srv", f"{first}/mcp/srv") in res.warnings


def test_duplicate_claim_reproducible_across_checkouts(tmp_path: Path):
    """A project with install history and a fresh checkout of the same config + lock end up
    with the same owner."""
    hist = _project(tmp_path / "hist", [_mcp("two")])
    sync(hist)  # two installs first...
    _add_first(hist, _mcp("one"))  # ...then one is listed above it
    sync(hist)

    fresh = tmp_path / "fresh" / "proj"
    fresh.mkdir(parents=True)
    (fresh / ".agentry.yml").write_bytes((hist / ".agentry.yml").read_bytes())
    lock_path(fresh).write_bytes(lock_path(hist).read_bytes())
    sync(fresh)

    assert _srv(hist) == _srv(fresh) == {"command": "from-one"}


def test_reorder_transfers_named_key_same_sync(tmp_path: Path):
    proj = _project(tmp_path, [_mcp("two"), _mcp("one")])
    sync(proj)
    assert _srv(proj) == {"command": "from-two"}

    _reorder(proj, "one/mcp/srv", "two/mcp/srv")
    res = sync(proj)
    assert _srv(proj) == {"command": "from-one"}  # transferred in one sync
    assert _w5_key("two/mcp/srv", "srv", "one/mcp/srv") in res.warnings
    assert _transfer(".mcp.json:mcpServers.srv", "one/mcp/srv", "two/mcp/srv") in res.updated


def test_transfer_blocked_by_user_edit(tmp_path: Path):
    """If the losing owner's entry was hand-edited, it stays (W4 for the loser) and the
    winner is refused (W1): nothing overwrites the user's edit."""
    proj = _project(tmp_path, [_mcp("two"), _mcp("one")])
    sync(proj)
    cfg = proj / ".mcp.json"
    doc = json.loads(cfg.read_text("utf-8"))
    doc["mcpServers"]["srv"] = {"command": "EDITED"}
    cfg.write_text(json.dumps(doc), "utf-8")
    edited = cfg.read_bytes()

    _reorder(proj, "one/mcp/srv", "two/mcp/srv")
    res = sync(proj)
    assert cfg.read_bytes() == edited
    assert any(
        w.startswith("two/mcp/srv") and "left in place on removal" in w for w in res.warnings
    )
    assert any(w.startswith("one/mcp/srv") and "not added by agentry" in w for w in res.warnings)


def test_w5_first_install_atomic_and_per_key_after(tmp_path: Path):
    proj = _project(tmp_path, [_mcp("two")])
    pack = tmp_path / "src-one" / "mcp" / "pack.json"
    pack.write_text(json.dumps({"srv": {"command": "p"}, "extra": {"command": "x"}}), "utf-8")
    store = ConfigStore.load(proj)
    store.doc["components"] = [
        {"source": "two", "type": "mcp", "name": "srv"},
        {"source": "one", "type": "mcp", "name": "pack"},
    ]
    store.save()

    # First install of `pack` into .mcp.json: srv is contested (two is earlier) -> atomic.
    res = sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"]
    assert "extra" not in servers
    assert _w5_key("one/mcp/pack", "srv", "two/mcp/srv") in res.warnings

    # After install: pack installed with only `extra`, then upstream adds `srv` -> per key.
    pack.write_text(json.dumps({"extra": {"command": "x"}}), "utf-8")
    sync(proj)
    assert json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"]["extra"] == {
        "command": "x"
    }
    pack.write_text(json.dumps({"srv": {"command": "p"}, "extra": {"command": "x2"}}), "utf-8")
    res = sync(proj)
    servers = json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"]
    assert servers["extra"] == {"command": "x2"}  # sibling still updates
    assert servers["srv"] == {"command": "from-two"}
    assert _w5_key("one/mcp/pack", "srv", "two/mcp/srv") in res.warnings


@pytest.mark.parametrize("first", ["merge", "link_merge"])
def test_merge_vs_link_merge_same_key_config_order(tmp_path: Path, first: str):
    """A plain-merge mcp component and a link+merge hook component that both write
    `.mcp.json:mcpServers.srv` share one claim order; strategy doesn't matter."""
    lm_root = tmp_path / "src-one" / "hooks"
    lm_root.mkdir(parents=True, exist_ok=True)
    proj = _project(
        tmp_path,
        [],
        profiles={
            "claude": {
                "hook": {
                    "strategy": "link+merge",
                    "dest": ".claude/lm/{name}",
                    "file": ".mcp.json",
                    "pointer": "mcpServers",
                }
            }
        },
    )
    (lm_root / "run.sh").write_text("echo\n", "utf-8")
    (lm_root / "hooks.json").write_text(json.dumps({"srv": {"command": "from-lm"}}), "utf-8")
    merge = {"source": "two", "type": "mcp", "name": "srv"}
    lm = {"source": "one", "type": "hook", "name": "hooks", "path": "hooks"}
    store = ConfigStore.load(proj)
    store.doc["components"] = [merge, lm] if first == "merge" else [lm, merge]
    store.save()

    res = sync(proj)
    if first == "merge":
        assert _srv(proj) == {"command": "from-two"}
        assert any(w.startswith("one/hook/hooks") and "listed earlier" in w for w in res.warnings)
    else:
        assert _srv(proj) == {"command": "from-lm"}
        assert _w5_key("two/mcp/srv", "srv", "one/hook/hooks") in res.warnings


# --- paths ------------------------------------------------------------------------------


def test_duplicate_link_path_first_in_config_wins(tmp_path: Path):
    """The architect's repro: two sources both ship commands/review.md. Today the later one
    silently wins with no warning; Rev 6: the earlier one wins and the other gets W5-path."""
    proj = _project(tmp_path, [_cmd("one"), _cmd("two")])
    res = sync(proj)
    assert _review(proj) == "# review from one\n"
    assert _w5_path("two/command/review", REVIEW, "one/command/review") in res.warnings
    (rec,) = [link for link in load_manifest(proj).links if link.path == REVIEW]
    assert rec.component == "one/command/review"


def test_duplicate_copy_path_first_in_config_wins(tmp_path: Path):
    profiles = {"claude": {"command": {"strategy": "copy", "dest": ".claude/commands/{name}.md"}}}
    proj = _project(tmp_path, [_cmd("one"), _cmd("two")], profiles=profiles)
    res = sync(proj)
    assert not (proj / REVIEW).is_symlink()
    assert _review(proj) == "# review from one\n"
    assert _w5_path("two/command/review", REVIEW, "one/command/review") in res.warnings


def test_link_vs_copy_same_path_cross_strategy_transfer(tmp_path: Path):
    """`two` (a link) owns the path; then `one` (a transform = real file) is listed first.
    The path switches strategy in one sync — no 'already exists and is not managed'."""
    proj = _project(tmp_path, [_cmd("two")])
    sync(proj)
    assert (proj / REVIEW).is_symlink()

    _add_first(proj, _cmd("one", transform=ComponentTransform(provider="strip-frontmatter")))
    res = sync(proj)
    assert not any("not managed" in w for w in res.warnings)
    assert (proj / REVIEW).is_file() and not (proj / REVIEW).is_symlink()
    assert _review(proj) == "# review from one\n"
    assert _w5_path("two/command/review", REVIEW, "one/command/review") in res.warnings


def test_link_merge_loses_as_pair(tmp_path: Path):
    """Two link+merge components on the same link path: the later one installs neither its
    link nor its merge half."""
    for name, key in (("one", "k1"), ("two", "k2")):
        root = tmp_path / f"src-{name}" / "hooks"
        root.mkdir(parents=True)
        (root / "run.sh").write_text(f"echo {name}\n", "utf-8")
        (root / "hooks.json").write_text(json.dumps({key: {"command": name}}), "utf-8")
    proj = _project(
        tmp_path,
        [
            Component(source="one", type=_C.HOOK, name="hooks", path="hooks"),
            Component(source="two", type=_C.HOOK, name="hooks", path="hooks"),
        ],
        profiles={
            "claude": {
                "hook": {
                    "strategy": "link+merge",
                    "dest": ".claude/lm/{name}",
                    "file": ".claude/settings.json",
                    "pointer": "hooks",
                }
            }
        },
    )
    res = sync(proj)
    hooks = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]
    assert "k1" in hooks and "k2" not in hooks  # loser's merge half not installed
    assert (proj / ".claude/lm/hooks/run.sh").read_text("utf-8") == "echo one\n"
    assert any(w.startswith("two/hook/hooks") and "listed earlier" in w for w in res.warnings)


def test_existing_last_write_wins_install_switches_to_winner(tmp_path: Path):
    """Upgrade path: the path is currently owned by `two` (what last-write-wins left). With
    `one` listed first, the next sync switches the content, fixes the manifest label, and
    reports the transfer exactly once; `two` keeps getting W5-path."""
    proj = _project(tmp_path, [_cmd("two")])
    sync(proj)
    _add_first(proj, _cmd("one"))

    res = sync(proj)
    assert _review(proj) == "# review from one\n"
    (rec,) = [link for link in load_manifest(proj).links if link.path == REVIEW]
    assert rec.component == "one/command/review"
    line = _transfer(REVIEW, "one/command/review", "two/command/review")
    assert res.updated.count(line) == 1

    again = sync(proj)
    assert line not in again.updated  # printed once, not every sync
    assert _w5_path("two/command/review", REVIEW, "one/command/review") in again.warnings


def test_unmanaged_file_at_contested_path_untouched(tmp_path: Path):
    proj = _project(tmp_path, [_cmd("one"), _cmd("two")])
    (proj / REVIEW).parent.mkdir(parents=True)
    (proj / REVIEW).write_text("MINE\n", "utf-8")
    res = sync(proj)
    assert _review(proj) == "MINE\n"
    assert not (proj / REVIEW).is_symlink()
    assert any("not managed" in w for w in res.warnings)  # the winner is refused, as before
    assert _w5_path("two/command/review", REVIEW, "one/command/review") in res.warnings


# --- arrays -----------------------------------------------------------------------------


def _hook_src(tmp_path: Path, name: str, events: dict) -> None:
    root = tmp_path / f"src-{name}" / "hooks"
    root.mkdir(parents=True, exist_ok=True)
    (root / "h.json").write_text(json.dumps({"hooks": events}), "utf-8")


def _g(cmd: str) -> dict:
    return {"hooks": [{"type": "command", "command": cmd}]}


def test_array_identical_groups_shared_not_w5(tmp_path: Path):
    proj = _project(tmp_path, [])
    for name in ("one", "two"):
        _hook_src(tmp_path, name, {"Stop": [_g("same")]})
    store = ConfigStore.load(proj)
    for name in ("one", "two"):
        store.add_component(Component(source=name, type=_C.HOOK, name="h"))
    store.save()
    res = sync(proj)
    stop = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]["Stop"]
    assert stop == [_g("same")]  # shared once
    assert not any("already provided by" in w for w in res.warnings)


@pytest.mark.parametrize("first", ["one", "two"])
def test_mixed_list_vs_object_config_order_w5(tmp_path: Path, first: str):
    """one ships Stop as a list, two as an object: a named unit, so config order decides."""
    proj = _project(tmp_path, [])
    _hook_src(tmp_path, "one", {"Stop": [_g("listy")]})
    _hook_src(tmp_path, "two", {"Stop": {"command": "objy"}})
    store = ConfigStore.load(proj)
    order = ["one", "two"] if first == "one" else ["two", "one"]
    for name in order:
        store.add_component(Component(source=name, type=_C.HOOK, name="h"))
    store.save()

    res = sync(proj)
    stop = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]["Stop"]
    second = order[1]
    assert stop == ([_g("listy")] if first == "one" else {"command": "objy"})
    assert (
        _w5_key(
            f"{second}/hook/h",
            "Stop",
            f"{first}/hook/h",
            pointer="hooks",
            file=".claude/settings.json",
        )
        in res.warnings
    )


# --- general ----------------------------------------------------------------------------


def test_duplicate_claims_sync_idempotent(tmp_path: Path):
    proj = _project(tmp_path, [_cmd("one"), _cmd("two"), _mcp("one"), _mcp("two")])
    first = sync(proj)
    snapshot = {f: (proj / f).read_bytes() for f in (".mcp.json",)}
    second = sync(proj)
    assert second.created == [] and second.updated == [] and second.removed == []
    assert sorted(second.warnings) == sorted(first.warnings)
    assert {f: (proj / f).read_bytes() for f in snapshot} == snapshot
    assert _w5_path("two/command/review", REVIEW, "one/command/review") in second.warnings


def test_status_doctor_report_loser_with_note(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, [_mcp("one"), _mcp("two")])
    sync(proj)
    rows, _ = status(proj)
    (loser,) = [r for r in rows if r.ref == "two/mcp/srv"]
    assert loser.state == "missing"
    assert loser.note and (
        "is provided by one/mcp/srv (listed earlier in .agentry.yml)" in loser.note
    )
    assert any(
        c.level == "warn" and "two/mcp/srv" in c.message and "listed earlier" in c.message
        for c in run_checks(proj)
    )
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["doctor", "--strict"]).exit_code == 1


# --- architect's additions to the plan ----------------------------------------------------


_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@e.x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e.x",
}


def _git_repo(path: Path, files: dict[str, str]) -> str:
    import os
    import subprocess

    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, "utf-8")
    env = {**os.environ, **_GIT_ENV}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=path, check=True, env=env, capture_output=True)
    return f"file://{path}"


def _lib_requiring(tmp_path: Path, deps: dict[str, str]) -> str:
    """A git source `lib` whose skill `top` requires skill `rev` from each dep repo given."""
    urls = {
        name: _git_repo(tmp_path / name, {"skills/rev/SKILL.md": f"# rev from {name}\n"})
        for name in deps
    }
    requires = [{"type": "skill", "name": "rev", "url": u, "ref": "main"} for u in urls.values()]
    return _git_repo(
        tmp_path / "lib",
        {
            "skills/top/SKILL.md": "# top\n",
            "agentry.yaml": json.dumps(
                {
                    "version": 1,
                    "provides": {
                        "skill": [{"name": "top", "path": "skills/top", "requires": requires}]
                    },
                }
            ),
        },
    )


def _transitive_project(tmp_path: Path, deps: dict[str, str], declared_rev: bool) -> Path:
    url_lib = _lib_requiring(tmp_path, deps)
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    if declared_rev:
        mine = tmp_path / "mine"
        (mine / "skills" / "rev").mkdir(parents=True)
        (mine / "skills" / "rev" / "SKILL.md").write_text("# rev from mine\n", "utf-8")
        store.add_source(Source(name="mine", type=SourceType.LOCAL, path=str(mine)))
        store.add_component(Component(source="mine", type=_C.SKILL, name="rev"))
    store.add_source(Source(name="lib", type=SourceType.GIT, url=url_lib, ref="main"))
    store.add_component(Component(source="lib", type=_C.SKILL, name="top"))
    store.save()
    return proj


def _rev_w5(res) -> str:
    (w,) = [w for w in res.warnings if ".claude/skills/rev is already installed by" in w]
    return w


def test_transitive_collision_hint_says_declare(tmp_path: Path):
    """A transitive component can't be reordered, so its W5 says to declare it instead."""
    proj = _transitive_project(tmp_path, {"libdep": ""}, declared_rev=True)
    res = sync(proj)
    assert (proj / ".claude/skills/rev/SKILL.md").read_text("utf-8") == "# rev from mine\n"
    w = _rev_w5(res)
    assert "installed by mine/skill/rev" in w  # declared wins
    assert "declare " in w.lower() and "in .agentry.yml" in w
    assert "reorder components" not in w  # the transitive loser can't be reordered


def _no_disable_of(w: str, loser: str) -> None:
    assert "Disable one of them" not in w and "disable one of them" not in w
    assert f"disable {loser}" not in w  # `agentry disable <transitive>` would fail


def test_transitive_loser_hint_names_declared_winner(tmp_path: Path):
    """Declared winner, transitive loser (W5-path): declaring the loser only helps if it goes
    *above* the winner; the only disable it may suggest is the declared winner's."""
    proj = _transitive_project(tmp_path, {"libdep": ""}, declared_rev=True)
    w = _rev_w5(sync(proj))
    loser = w.split(":", 1)[0]
    assert w == (
        f"{loser}: .claude/skills/rev is already installed by mine/skill/rev — skipped on "
        f"claude. Declare {loser} in .agentry.yml above mine/skill/rev, or disable mine/skill/rev."
    )
    assert "to give it priority" not in w
    _no_disable_of(w, loser)


def test_transitive_winner_hint_says_give_it_priority(tmp_path: Path):
    """Both claimants transitive (two deps ship the same skill), W5-path: the loser is told
    to declare itself to give it priority, and no disable is suggested at all."""
    proj = _transitive_project(tmp_path, {"depa": "", "depb": ""}, declared_rev=False)
    w = _rev_w5(sync(proj))
    loser = w.split(":", 1)[0]
    assert w.endswith(
        f"skipped on claude. To change that, declare {loser} in .agentry.yml to give it priority."
    )
    assert "isable" not in w
    assert "reorder components" not in w


def test_transitive_loser_transfer_line_names_declared_winner(tmp_path: Path):
    """Transfer line, declared winner over a transitive incumbent: the rev skill was installed
    transitively; declaring mine/skill/rev takes it over and says how to keep the old one."""
    proj = _transitive_project(tmp_path, {"libdep": ""}, declared_rev=False)
    sync(proj)  # the transitive libdep/skill/rev owns .claude/skills/rev
    mine = tmp_path / "mine"
    (mine / "skills" / "rev").mkdir(parents=True)
    (mine / "skills" / "rev" / "SKILL.md").write_text("# rev from mine\n", "utf-8")
    store = ConfigStore.load(proj)
    store.add_source(Source(name="mine", type=SourceType.LOCAL, path=str(mine)))
    store.doc["components"].insert(
        0, Component(source="mine", type=_C.SKILL, name="rev").model_dump(mode="json")
    )
    store.save()

    res = sync(proj)
    (line,) = [u for u in res.updated if u.startswith(".claude/skills/rev now comes from")]
    loser = line.split(" instead of ", 1)[1].split(" ", 1)[0]
    assert line.startswith(f".claude/skills/rev now comes from mine/skill/rev instead of {loser}")
    assert (
        f"declare {loser} in .agentry.yml above mine/skill/rev, or disable mine/skill/rev" in line
    )
    _no_disable_of(line, loser)


def test_transitive_winner_transfer_line_says_give_it_priority(tmp_path: Path):
    """Transfer line, both transitive: lib first requires only depb; then depa is required
    ahead of it, so depa takes the path over on `update`."""
    url_depa = _git_repo(tmp_path / "depa", {"skills/rev/SKILL.md": "# rev from depa\n"})
    url_depb = _git_repo(tmp_path / "depb", {"skills/rev/SKILL.md": "# rev from depb\n"})
    lib = tmp_path / "lib"

    def lib_requires(*urls: str) -> str:
        reqs = [{"type": "skill", "name": "rev", "url": u, "ref": "main"} for u in urls]
        return json.dumps(
            {
                "version": 1,
                "provides": {"skill": [{"name": "top", "path": "skills/top", "requires": reqs}]},
            }
        )

    url_lib = _git_repo(
        lib, {"skills/top/SKILL.md": "# top\n", "agentry.yaml": lib_requires(url_depb)}
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["claude"])
    store.add_source(Source(name="lib", type=SourceType.GIT, url=url_lib, ref="main"))
    store.add_component(Component(source="lib", type=_C.SKILL, name="top"))
    store.save()
    sync(proj)
    assert "depb" in (proj / ".claude/skills/rev/SKILL.md").read_text("utf-8")

    import os
    import subprocess

    (lib / "agentry.yaml").write_text(lib_requires(url_depa, url_depb), "utf-8")
    env = {**os.environ, **_GIT_ENV}
    subprocess.run(["git", "commit", "-qam", "depa first"], cwd=lib, check=True, env=env)
    res = sync(proj, update=True)
    (line,) = [u for u in res.updated if u.startswith(".claude/skills/rev now comes from")]
    loser = line.split(" instead of ", 1)[1].split(" ", 1)[0]
    assert f"declare {loser} in .agentry.yml to give it priority" in line
    assert "isable" not in line


# --- the architect's two small fixes ---------------------------------------------------


def test_same_component_two_targets_same_file_no_self_w5(tmp_path: Path):
    """One component merging into one file+pointer through two targets (two profiles that
    share a config file) is not a duplicate claim against itself."""
    proj = _project(
        tmp_path,
        [_mcp("one")],
        profiles={
            t: {"mcp": {"strategy": "merge", "file": "shared.json", "pointer": "servers"}}
            for t in ("tool1", "tool2")
        },
    )
    store = ConfigStore.load(proj)
    store.doc["targets"] = ["tool1", "tool2"]
    store.save()
    res = sync(proj)
    assert not any("already provided by" in w for w in res.warnings)
    shared = json.loads((proj / "shared.json").read_text("utf-8"))["servers"]
    assert shared == {"srv": {"command": "from-one"}}


def test_transfer_with_failed_winner_install_prints_no_transfer_line(tmp_path: Path):
    """The loser's managed link was replaced by the user's own file, so the winner's install
    hits an unmanaged path: no 'now comes from' line, and the user's file is untouched."""
    proj = _project(tmp_path, [_cmd("two")])
    sync(proj)
    (proj / REVIEW).unlink()
    (proj / REVIEW).write_text("MINE\n", "utf-8")  # the user's own file, unmanaged
    _add_first(proj, _cmd("one"))

    res = sync(proj)
    assert not any("now comes from" in u for u in res.updated)
    assert _review(proj) == "MINE\n" and not (proj / REVIEW).is_symlink()
    assert any("not managed" in w for w in res.warnings)


def test_case_variant_paths_collide(tmp_path: Path):
    """`commands/Review.md` and `commands/review.md` are one path on case-insensitive file
    systems, so they collide on every OS: the earlier-listed component wins."""
    proj = _project(tmp_path, [])
    review_upper = tmp_path / "src-one" / "commands" / "Review.md"
    (tmp_path / "src-one" / "commands" / "review.md").rename(review_upper)
    store = ConfigStore.load(proj)
    store.add_component(Component(source="one", type=_C.COMMAND, name="Review"))
    store.add_component(Component(source="two", type=_C.COMMAND, name="review"))
    store.save()

    res = sync(proj)
    assert _w5_path("two/command/review", REVIEW, "one/command/Review") in res.warnings
    recs = [link for link in load_manifest(proj).links if link.path.casefold() == REVIEW]
    assert [r.component for r in recs] == ["one/command/Review"]
