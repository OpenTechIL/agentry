"""D1 + D2 (PM rulings on R3): OpenCode tools keep their file extension and install as copies.

OpenCode loads only `*.ts`/`*.js` from `.opencode/tools/`, so an extensionless install was
silently ignored (D1). A symlinked tool that imports `@opencode-ai/plugin` resolves the import
from the link's real path and breaks every opencode command (D2), so the opencode tool default
is `copy`. Both are data on the driver; a `target_profiles` override is still honoured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import sync

_C = ComponentType
_TS = b'export default { description: "fmt", args: {}, async execute() { return "ok" } }\n'


def _project(tmp_path: Path, targets=("opencode",), *, ext=".ts", profiles=None) -> Path:
    src = tmp_path / "src"
    (src / "tools").mkdir(parents=True)
    (src / "tools" / f"fmt{ext}").write_bytes(_TS)
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, list(targets))
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.TOOL, name="fmt", path=f"tools/fmt{ext}"))
    if profiles:
        store.doc["target_profiles"] = profiles
    store.save()
    return proj


def _set_profiles(proj: Path, profiles: dict | None) -> None:
    store = ConfigStore.load(proj)
    if profiles is None:
        store.doc.pop("target_profiles", None)
    else:
        store.doc["target_profiles"] = profiles
    store.save()


def _tools(proj: Path) -> list[str]:
    d = proj / ".opencode" / "tools"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


# --- the new default ---------------------------------------------------------------------


@pytest.mark.parametrize("ext", [".ts", ".js"])
def test_opencode_tool_keeps_extension_and_is_a_copy(tmp_path: Path, ext: str):
    proj = _project(tmp_path, ext=ext)
    sync(proj)
    dest = proj / ".opencode" / "tools" / f"fmt{ext}"
    assert dest.is_file() and not dest.is_symlink()  # a real copy (D2)
    assert dest.read_bytes() == _TS
    assert _tools(proj) == [f"fmt{ext}"]  # and no extensionless twin (D1)


def test_copy_is_refreshed_by_sync_when_the_source_changes(tmp_path: Path):
    """The accepted copy trade-off: store edits show up on the next sync, not live."""
    proj = _project(tmp_path)
    sync(proj)
    new = b'export default { description: "fmt v2", args: {}, async execute() { return "v2" } }\n'
    (tmp_path / "src" / "tools" / "fmt.ts").write_bytes(new)
    sync(proj)
    assert (proj / ".opencode/tools/fmt.ts").read_bytes() == new


def test_removal_deletes_the_copy(tmp_path: Path):
    proj = _project(tmp_path)
    sync(proj)
    assert (proj / ".opencode/tools/fmt.ts").is_file()
    store = ConfigStore.load(proj)
    store.doc["components"] = []
    store.save()
    sync(proj)
    assert not (proj / ".opencode/tools/fmt.ts").exists()
    assert not (proj / ".opencode").exists()  # agentry's own folders go too (#48)


# --- upgrade from the old extensionless link ---------------------------------------------

_LEGACY = {"opencode": {"tool": {"strategy": "link", "dest": ".opencode/tools/{name}"}}}


def test_upgrade_from_extensionless_link_replaces_it_cleanly(tmp_path: Path):
    """An install by an older agentry (simulated with the old rule as an override): the next
    sync with the new default removes agentry's `fmt` link and installs `fmt.ts` as a copy."""
    proj = _project(tmp_path, profiles=_LEGACY)
    sync(proj)
    assert (proj / ".opencode/tools/fmt").is_symlink()
    _set_profiles(proj, None)

    res = sync(proj)
    assert _tools(proj) == ["fmt.ts"]
    assert not (proj / ".opencode/tools/fmt").is_symlink()
    assert (proj / ".opencode/tools/fmt.ts").read_bytes() == _TS
    assert res.removed and res.created  # the swap is reported
    again = sync(proj)
    assert again.created == [] and again.updated == [] and again.removed == []


def test_upgrade_leaves_a_user_file_named_like_the_old_dest(tmp_path: Path):
    """Only agentry's own old link is removed: a user's own `.opencode/tools/notes` stays."""
    proj = _project(tmp_path, profiles=_LEGACY)
    sync(proj)
    (proj / ".opencode/tools/notes").write_bytes(b"mine\n")
    _set_profiles(proj, None)
    sync(proj)
    assert (proj / ".opencode/tools/notes").read_bytes() == b"mine\n"
    assert _tools(proj) == ["fmt.ts", "notes"]


# --- overrides and other targets ---------------------------------------------------------


def test_target_profiles_link_override_still_honoured(tmp_path: Path):
    """Guard: a user who wants a symlink gets one (and owns the D2 consequence)."""
    profiles = {"opencode": {"tool": {"strategy": "link", "dest": ".opencode/tools/{name}.ts"}}}
    proj = _project(tmp_path, profiles=profiles)
    sync(proj)
    dest = proj / ".opencode/tools/fmt.ts"
    assert dest.is_symlink()
    assert dest.read_bytes() == _TS


def test_claude_tool_install_unchanged(tmp_path: Path):
    """Guard: other targets keep their rule — claude still links `.claude/tools/{name}`."""
    proj = _project(tmp_path, targets=("claude",))
    sync(proj)
    dest = proj / ".claude/tools/fmt"
    assert dest.is_symlink()
    assert not (proj / ".claude/tools/fmt.ts").exists()


def test_opencode_tool_install_is_idempotent(tmp_path: Path):
    proj = _project(tmp_path, targets=("claude", "opencode"))
    sync(proj)
    before = (proj / ".opencode/tools/fmt.ts").stat().st_mtime_ns
    again = sync(proj)
    assert again.created == [] and again.updated == [] and again.removed == []
    assert (proj / ".opencode/tools/fmt.ts").stat().st_mtime_ns == before


def test_user_file_at_new_dest_is_refused_and_kept(tmp_path: Path):
    """A user's own `.opencode/tools/fmt.ts` before the first sync is never overwritten."""
    proj = _project(tmp_path)
    (proj / ".opencode/tools").mkdir(parents=True)
    (proj / ".opencode/tools/fmt.ts").write_bytes(b"// mine\n")
    res = sync(proj)
    assert (proj / ".opencode/tools/fmt.ts").read_bytes() == b"// mine\n"
    assert any(
        ".opencode/tools/fmt.ts already exists and is not managed by agentry" in w
        for w in res.warnings
    )
