"""T21 / ADR-001 Rev 5: array mode for event-keyed hook lists.

An event key whose desired value is a list is merged *per element*: agentry appends its
groups next to the user's, shares an identical element between components by reference
count, updates its own elements in place, and on removal deletes only elements it still
owns. The user's elements and their order are never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.installers.merge import fingerprint
from agentry.lockfile import lock_path
from agentry.manifest import load_manifest, manifest_path
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import status, sync

_C = ComponentType
SETTINGS = ".claude/settings.json"


def _g(cmd: str) -> dict:
    return {"matcher": "*", "hooks": [{"type": "command", "command": cmd}]}


def _write_hook(src: Path, name: str, events: dict[str, list[dict]]) -> None:
    (src / "hooks").mkdir(parents=True, exist_ok=True)
    (src / "hooks" / f"{name}.json").write_text(json.dumps({"hooks": events}), encoding="utf-8")


def _project(tmp_path: Path, hooks: dict[str, dict], targets=(Target.CLAUDE,)):
    """hooks: {component name: {event: [groups]}}."""
    src = tmp_path / "src"
    for name, events in hooks.items():
        _write_hook(src, name, events)
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, list(targets)).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for name in hooks:
        store.add_component(Component(source="s", type=_C.HOOK, name=name))
    store.save()
    return proj, src


def _user_settings(proj: Path, events: dict[str, object], file: str = SETTINGS) -> None:
    path = proj / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": events}, indent=2), encoding="utf-8")


def _hooks(proj: Path, file: str = SETTINGS) -> dict:
    path = proj / file
    return json.loads(path.read_text(encoding="utf-8")).get("hooks", {}) if path.is_file() else {}


def _cmds(proj: Path, event: str, file: str = SETTINGS) -> list[str]:
    return [h["command"] for g in _hooks(proj, file).get(event, []) for h in g["hooks"]]


def _set(proj: Path, ref: str, enabled: bool) -> None:
    store = ConfigStore.load(proj)
    store.set_enabled(ref, enabled)
    store.save()


def _set_keep(proj: Path, name: str, keep: list[str]) -> None:
    store = ConfigStore.load(proj)
    for c in store.doc["components"]:
        if c["name"] == name:
            c["keep"] = keep
    store.save()


def _entry(proj: Path, ref: str, key: str):
    (rec,) = [m for m in load_manifest(proj).merges if m.component == ref]
    return rec.entries[key]


# --- install and coexistence -----------------------------------------------------------


def test_hook_merge_preserves_user_entries_in_same_event(tmp_path: Path):
    proj, _ = _project(tmp_path, {"guard": {"PreToolUse": [_g("ours")]}})
    _user_settings(proj, {"PreToolUse": [_g("USER")]})

    sync(proj)
    assert _cmds(proj, "PreToolUse") == ["USER", "ours"]  # appended, user order kept
    before = (proj / SETTINGS).read_bytes()
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert (proj / SETTINGS).read_bytes() == before
    assert [r.state for r in status(proj)[0]] == ["ok"]

    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert _cmds(proj, "PreToolUse") == ["USER"]


def test_two_hook_components_same_event_both_installed(tmp_path: Path):
    proj, _ = _project(
        tmp_path, {"fmt": {"PostToolUse": [_g("fmt")]}, "lint": {"PostToolUse": [_g("lint")]}}
    )
    sync(proj)
    assert _cmds(proj, "PostToolUse") == ["fmt", "lint"]

    _set(proj, "s/hook/fmt", False)
    sync(proj)
    assert _cmds(proj, "PostToolUse") == ["lint"]


def test_identical_group_from_two_components_shared_refcount(tmp_path: Path):
    proj, _ = _project(
        tmp_path,
        {
            "fmt": {"PostToolUse": [_g("fmt")]},
            "lint": {"PostToolUse": [_g("lint")]},
            "dup": {"PostToolUse": [_g("fmt")]},  # identical to fmt's element
        },
    )
    res = sync(proj)
    assert _cmds(proj, "PostToolUse") == ["fmt", "lint"]  # shared, not appended twice
    assert not any("is already provided by" in w for w in res.warnings)

    _set(proj, "s/hook/fmt", False)
    sync(proj)
    assert _cmds(proj, "PostToolUse") == ["fmt", "lint"]  # dup still claims it
    _set(proj, "s/hook/dup", False)
    sync(proj)
    assert _cmds(proj, "PostToolUse") == ["lint"]
    _set(proj, "s/hook/lint", False)
    sync(proj)
    assert "PostToolUse" not in _hooks(proj)  # created_key handed over, deleted when empty


def test_user_identical_group_recorded_none_never_removed(tmp_path: Path):
    proj, _ = _project(tmp_path, {"guard": {"PreToolUse": [_g("ours")]}})
    _user_settings(proj, {"PreToolUse": [_g("ours")]})  # the user already has it

    sync(proj)
    assert _cmds(proj, "PreToolUse") == ["ours"]  # not duplicated
    assert _entry(proj, "s/hook/guard", "PreToolUse").items == [None]

    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert _cmds(proj, "PreToolUse") == ["ours"]  # the user's, never removed


# --- updates ----------------------------------------------------------------------------


def test_hook_source_update_replaces_in_place(tmp_path: Path):
    proj, src = _project(tmp_path, {"guard": {"PreToolUse": [_g("v1")]}})
    _user_settings(proj, {"PreToolUse": [_g("USER")]})
    sync(proj)
    hooks = _hooks(proj)
    hooks["PreToolUse"].append(_g("USER2"))  # user adds after ours
    _user_settings(proj, hooks)

    _write_hook(src, "guard", {"PreToolUse": [_g("v2")]})
    res = sync(proj)
    assert _cmds(proj, "PreToolUse") == ["USER", "v2", "USER2"]  # same index
    assert any(SETTINGS in u for u in res.updated)


def test_source_drops_element_removes_only_that_one(tmp_path: Path):
    proj, src = _project(tmp_path, {"pack": {"PreToolUse": [_g("a"), _g("b")]}})
    _user_settings(proj, {"PreToolUse": [_g("USER")]})
    sync(proj)
    assert _cmds(proj, "PreToolUse") == ["USER", "a", "b"]

    _write_hook(src, "pack", {"PreToolUse": [_g("b")]})
    sync(proj)
    assert _cmds(proj, "PreToolUse") == ["USER", "b"]


def test_link_merge_template_change_rewrites_all_in_place(tmp_path: Path):
    """A dest-template change rewrites every one of our (link-rewritten) elements at its
    own index, pairing old and new positionally; the user's element in between stays."""
    src = tmp_path / "src"
    (src / "hooks").mkdir(parents=True)
    for s in ("a.sh", "b.sh"):
        (src / "hooks" / s).write_text("echo\n", encoding="utf-8")
    (src / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [_g("${PLUGIN_ROOT}/a.sh"), _g("${PLUGIN_ROOT}/b.sh")]}}),
        encoding="utf-8",
    )
    proj = tmp_path / "proj"
    proj.mkdir()

    def config(dirname: str) -> str:
        return f"""version: 1
targets: [claude]
sources:
  - name: s
    type: local
    path: {src}
components:
  - source: s
    type: hook
    name: hooks
    path: hooks
target_profiles:
  claude:
    hook:
      strategy: link+merge
      dest: ".claude/hooks/{dirname}/{{name}}"
      file: .claude/settings.json
      pointer: hooks
      rewrite_from: "${{PLUGIN_ROOT}}"
      rewrite_to: "R/{dirname}"
"""

    (proj / ".agentry.yml").write_text(config("old"), encoding="utf-8")
    sync(proj)
    hooks = _hooks(proj)
    hooks["Stop"].insert(1, _g("USER"))  # user's element between ours
    _user_settings(proj, hooks)

    (proj / ".agentry.yml").write_text(config("new"), encoding="utf-8")
    sync(proj)
    assert _cmds(proj, "Stop") == ["R/new/a.sh", "USER", "R/new/b.sh"]
    assert (proj / ".claude/hooks/new/hooks").is_symlink()
    assert not (proj / ".claude/hooks/old/hooks").exists()


# --- removal and edits --------------------------------------------------------------------


def test_hook_uninstall_removes_only_appended_groups(tmp_path: Path):
    # agentry created the key: removing our only element deletes the key.
    proj, _ = _project(tmp_path / "a", {"guard": {"Stop": [_g("ours")]}})
    sync(proj)
    assert _entry(proj, "s/hook/guard", "Stop").created_key is True
    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert "Stop" not in _hooks(proj)

    # agentry created the key but the user added to it: the key stays with theirs.
    proj, _ = _project(tmp_path / "b", {"guard": {"Stop": [_g("ours")]}})
    sync(proj)
    hooks = _hooks(proj)
    hooks["Stop"].append(_g("USER"))
    _user_settings(proj, hooks)
    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert _cmds(proj, "Stop") == ["USER"]

    # the user's key: never deleted, only our element goes.
    proj, _ = _project(tmp_path / "c", {"guard": {"Stop": [_g("ours")]}})
    _user_settings(proj, {"Stop": [_g("USER")]})
    sync(proj)
    assert _entry(proj, "s/hook/guard", "Stop").created_key is False
    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert _cmds(proj, "Stop") == ["USER"]


def test_edited_element_w2_not_reappended_and_left_on_disable(tmp_path: Path):
    proj, _ = _project(tmp_path, {"guard": {"Stop": [_g("ours")]}})
    _user_settings(proj, {"Stop": [_g("USER")]})
    sync(proj)
    hooks = _hooks(proj)
    hooks["Stop"][1] = _g("ours-EDITED")
    _user_settings(proj, hooks)

    res = sync(proj)
    assert _cmds(proj, "Stop") == ["USER", "ours-EDITED"]  # not re-appended
    assert any(
        "1 hook group of 'hooks.Stop' in .claude/settings.json no longer matches what agentry "
        "installed (edited or removed) — left as is." in w
        for w in res.warnings
    )
    (row,) = status(proj)[0]
    assert row.state == "drift" and row.note and "edited or removed by hand" in row.note

    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert _cmds(proj, "Stop") == ["USER", "ours-EDITED"]


# --- keep ------------------------------------------------------------------------------


def test_keep_array_key_stops_appends_and_counts_as_claim(tmp_path: Path):
    proj, src = _project(tmp_path, {"fmt": {"PostToolUse": [_g("v1")]}})
    sync(proj)
    _set_keep(proj, "fmt", ["PostToolUse"])
    sync(proj)
    before = (proj / SETTINGS).read_bytes()

    _write_hook(src, "fmt", {"PostToolUse": [_g("v2")]})
    sync(proj)
    assert (proj / SETTINGS).read_bytes() == before  # no writes on a kept key
    assert _cmds(proj, "PostToolUse") == ["v1"]
    assert _entry(proj, "s/hook/fmt", "PostToolUse").items  # still counts as a claim

    _set(proj, "s/hook/fmt", False)
    sync(proj)
    assert _cmds(proj, "PostToolUse") == ["v1"]  # a kept component's elements stay


def test_kept_shared_element_survives_other_component_disable(tmp_path: Path):
    """A keeps PostToolUse; B shares the identical element. Disabling B must not remove it,
    because A's kept record still claims it."""
    proj, _ = _project(
        tmp_path, {"a": {"PostToolUse": [_g("shared")]}, "b": {"PostToolUse": [_g("shared")]}}
    )
    res = sync(proj)
    assert _cmds(proj, "PostToolUse") == ["shared"]
    # Both genuinely claim the one element (array-mode sharing, not a W5 loser).
    assert not any("is already provided by" in w for w in res.warnings)
    fp = fingerprint(_g("shared"))
    assert _entry(proj, "s/hook/a", "PostToolUse").items == [fp]
    assert _entry(proj, "s/hook/b", "PostToolUse").items == [fp]
    _set_keep(proj, "a", ["PostToolUse"])
    sync(proj)

    _set(proj, "s/hook/b", False)
    sync(proj)
    assert _cmds(proj, "PostToolUse") == ["shared"]


# --- conflicts ----------------------------------------------------------------------------


def test_shape_conflict_list_vs_object_refused(tmp_path: Path):
    # First install: atomic. The user's Stop is an object; nothing of the component lands.
    proj, _ = _project(
        tmp_path / "first", {"pack": {"Stop": [_g("s")], "SessionStart": [_g("ss")]}}
    )
    _user_settings(proj, {"Stop": {"x": 1}})
    before = (proj / SETTINGS).read_bytes()
    res = sync(proj)
    assert any(
        "already has 'hooks.Stop' (not added by agentry) as an object, not a list" in w
        for w in res.warnings
    )
    assert (proj / SETTINGS).read_bytes() == before

    # After install: per key. Stop turns into an object; SessionStart still updates.
    proj, src = _project(
        tmp_path / "after", {"pack": {"Stop": [_g("s")], "SessionStart": [_g("ss")]}}
    )
    sync(proj)
    hooks = _hooks(proj)
    hooks["Stop"] = {"x": 1}
    _user_settings(proj, hooks)
    _write_hook(src, "pack", {"Stop": [_g("s2")], "SessionStart": [_g("ss2")]})
    sync(proj)
    assert _hooks(proj)["Stop"] == {"x": 1}
    assert _cmds(proj, "SessionStart") == ["ss2"]

    # Mixed mode: one component ships Stop as a list, another as an object: W5.
    proj, _ = _project(
        tmp_path / "mixed",
        {"listy": {"Stop": [_g("l")]}, "objy": {"Stop": {"command": "o"}}},  # type: ignore[dict-item]
    )
    res = sync(proj)
    assert any("is already provided by" in w for w in res.warnings)


# --- converting PR1 and v1 records ------------------------------------------------------


def _rewrite_record(proj: Path, key: str, entry: dict) -> None:
    mp = manifest_path(proj)
    payload = json.loads(mp.read_text(encoding="utf-8"))
    for rec in payload["merges"]:
        if key in rec.get("entries", {}):
            rec["entries"][key] = entry
    mp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_pr1_named_list_record_converts_exactly(tmp_path: Path):
    proj, _ = _project(tmp_path, {"guard": {"Stop": [_g("a"), _g("b")]}})
    sync(proj)
    value = _hooks(proj)["Stop"]
    _rewrite_record(proj, "Stop", {"mode": "named", "fingerprint": fingerprint(value)})

    sync(proj)
    e = _entry(proj, "s/hook/guard", "Stop")
    assert e.mode == "array"
    assert e.items == [fingerprint(_g("a")), fingerprint(_g("b"))]
    assert e.created_key is True
    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert "Stop" not in _hooks(proj)


def test_pr1_preexisting_list_converts_to_user_slots(tmp_path: Path):
    proj, _ = _project(tmp_path, {"guard": {"Stop": [_g("a")]}})
    sync(proj)
    value = _hooks(proj)["Stop"]
    _rewrite_record(
        proj, "Stop", {"mode": "named", "fingerprint": fingerprint(value), "preexisting": True}
    )

    sync(proj)
    e = _entry(proj, "s/hook/guard", "Stop")
    assert e.mode == "array" and e.items == [None]
    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert _cmds(proj, "Stop") == ["a"]  # the user's element, never removed


def test_unproven_list_claims_only_identical_elements(tmp_path: Path):
    """A v1 record (no proof) over [ours, USER]: only `ours` (identical to what agentry
    would install) is claimed; USER stays, and the key is never deleted."""
    proj, _ = _project(tmp_path, {"guard": {"Stop": [_g("ours")]}})
    sync(proj)
    hooks = _hooks(proj)
    hooks["Stop"].append(_g("USER"))
    _user_settings(proj, hooks)
    mp = manifest_path(proj)
    payload = json.loads(mp.read_text(encoding="utf-8"))
    payload["version"] = 1
    for rec in payload["merges"]:
        rec.pop("entries", None)
    mp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    sync(proj)
    e = _entry(proj, "s/hook/guard", "Stop")
    assert e.mode == "array" and e.items == [fingerprint(_g("ours"))]
    assert e.created_key is False
    _set(proj, "s/hook/guard", False)
    sync(proj)
    assert _cmds(proj, "Stop") == ["USER"]


# --- repeat runs ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, file, event",
    [
        (Target.CLAUDE, ".claude/settings.json", "SessionStart"),
        (Target.GEMINI, ".gemini/settings.json", "SessionStart"),
        (Target.WINDSURF, ".windsurf/hooks.json", "pre_run_command"),
    ],
    ids=["claude", "gemini", "windsurf"],
)
def test_array_mode_second_sync_is_byte_identical(tmp_path: Path, target, file, event):
    proj, _ = _project(
        tmp_path,
        {"a": {event: [_g("a1"), _g("a2")]}, "b": {event: [_g("b1"), _g("a1")]}},
        targets=(target,),
    )
    _user_settings(proj, {event: [_g("USER")]}, file=file)
    sync(proj)
    assert _cmds(proj, event, file) == ["USER", "a1", "a2", "b1"]
    before = (proj / file).read_bytes()
    lock_before = lock_path(proj).read_bytes()

    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert (proj / file).read_bytes() == before
    assert lock_path(proj).read_bytes() == lock_before


# --- architect review (Rev 5 follow-ups) -------------------------------------------------


def test_user_slot_upstream_change_does_not_duplicate(tmp_path: Path):
    """A slot satisfied by the user's identical element (None) must not grow a second copy
    when upstream changes: the user's copy is left as-is with a per-element warning, and
    ours installs only once the user deletes theirs."""
    proj, src = _project(tmp_path, {"guard": {"PreToolUse": [_g("ours")]}})
    _user_settings(proj, {"PreToolUse": [_g("ours")]})  # the user's copy == ours
    sync(proj)
    assert _entry(proj, "s/hook/guard", "PreToolUse").items == [None]
    user_bytes = json.dumps(_hooks(proj)["PreToolUse"][0], sort_keys=True)

    _write_hook(src, "guard", {"PreToolUse": [_g("ours-v2")]})
    res = sync(proj)
    assert _cmds(proj, "PreToolUse") == ["ours"]  # no second copy appended
    assert json.dumps(_hooks(proj)["PreToolUse"][0], sort_keys=True) == user_bytes
    assert any(
        "your copy of s/hook/guard's 'hooks.PreToolUse' hook group in .claude/settings.json "
        "differs from upstream — keeping yours"
        in w
        and "`agentry keep s/hook/guard PreToolUse`" in w
        and "(this stops s/hook/guard adding hooks to PreToolUse)" in w
        for w in res.warnings
    )
    (row,) = status(proj)[0]
    assert row.state == "drift"
    assert row.note and "(this stops s/hook/guard adding hooks to PreToolUse)" in row.note

    _user_settings(proj, {"PreToolUse": []})  # the user removes their copy
    sync(proj)
    assert _cmds(proj, "PreToolUse") == ["ours-v2"]


@pytest.mark.parametrize("config_order", ["creator_first", "other_first"])
def test_created_key_handover_persists_both_orders(tmp_path: Path, config_order):
    """A creates the event key, B appends. Disable A, then B: the key is gone either way
    (no leftover `[]`), and between the two the hand-over of `created_key` is persisted."""
    proj, _ = _project(tmp_path, {"creator": {"Stop": [_g("c")]}})
    sync(proj)  # `creator` creates the Stop key
    _write_hook(tmp_path / "src", "other", {"Stop": [_g("o")]})
    store = ConfigStore.load(proj)
    other = Component(source="s", type=_C.HOOK, name="other")
    if config_order == "other_first":
        store.doc["components"].insert(0, other.model_dump(mode="json", exclude_none=True))
    else:
        store.add_component(other)
    store.save()
    sync(proj)
    assert _cmds(proj, "Stop") == ["c", "o"]
    assert _entry(proj, "s/hook/creator", "Stop").created_key is True

    _set(proj, "s/hook/creator", False)
    sync(proj)
    assert _cmds(proj, "Stop") == ["o"]
    assert _entry(proj, "s/hook/other", "Stop").created_key is True  # handed over, saved

    _set(proj, "s/hook/other", False)
    sync(proj)
    assert "Stop" not in _hooks(proj)
    assert "[]" not in (proj / SETTINGS).read_text(encoding="utf-8")


def test_created_key_deleted_when_both_claimants_go_in_one_sync(tmp_path: Path):
    """Both claimants disabled in the same sync, the creator processed last: the emptied key
    is still deleted."""
    proj, _ = _project(tmp_path, {"creator": {"Stop": [_g("c")]}})
    sync(proj)
    _write_hook(tmp_path / "src", "other", {"Stop": [_g("o")]})
    store = ConfigStore.load(proj)
    store.doc["components"].insert(
        0,
        Component(source="s", type=_C.HOOK, name="other").model_dump(
            mode="json", exclude_none=True
        ),
    )
    store.save()
    sync(proj)
    assert _cmds(proj, "Stop") == ["c", "o"]  # both genuinely claim (no W5 loser)
    _set(proj, "s/hook/other", False)
    _set(proj, "s/hook/creator", False)
    sync(proj)
    assert "Stop" not in _hooks(proj)
