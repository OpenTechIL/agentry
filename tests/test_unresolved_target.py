"""T17: an undefined target fails fast — before any resolve, write or orphan pass — so a
typo in `targets:` can never uninstall the real target's components (FDE D4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.lockfile import lock_path
from agentry.manifest import manifest_path
from agentry.models import Component, ComponentType, Source, SourceType, Target
from agentry.reconcile import status, sync
from agentry.resolver import ResolveError
from agentry.targets import UnresolvedTargetError, describe_unresolved, suggest_target

_C = ComponentType
runner = CliRunner()


def _project(tmp_path: Path, local_source: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=_C.SKILL, name="code-reviewer"))
    store.add_component(Component(source="s", type=_C.MCP, name="github"))
    store.save()
    return proj


def _set_targets(proj: Path, targets: list[str]) -> None:
    store = ConfigStore.load(proj)
    store.doc["targets"] = targets
    store.save()


def _snapshot(proj: Path) -> dict[str, bytes | str]:
    """Every file under the project (symlinks by target), excluding the config itself."""
    snap: dict[str, bytes | str] = {}
    for p in sorted(proj.rglob("*")):
        rel = str(p.relative_to(proj))
        if rel == ".agentry.yml":
            continue
        if p.is_symlink():
            snap[rel] = "->" + str(p.readlink())
        elif p.is_file():
            snap[rel] = p.read_bytes()
    return snap


def test_unresolved_target_error_is_a_resolve_error():
    assert issubclass(UnresolvedTargetError, ResolveError)


def test_sync_typo_target_exits_1_and_touches_nothing(tmp_path, local_source, monkeypatch):
    proj = _project(tmp_path, local_source)
    sync(proj)
    assert (proj / ".claude/skills/code-reviewer").is_symlink()
    before = _snapshot(proj)
    lock_before = lock_path(proj).read_bytes()
    manifest_before = manifest_path(proj).read_bytes()

    _set_targets(proj, ["clade"])
    with pytest.raises(UnresolvedTargetError):
        sync(proj)
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1

    assert _snapshot(proj) == before  # real target's links/merges untouched
    assert lock_path(proj).read_bytes() == lock_before
    assert manifest_path(proj).read_bytes() == manifest_before


def test_did_you_mean_suggestion(tmp_path, local_source):
    proj = _project(tmp_path, local_source)
    _set_targets(proj, ["clade"])
    with pytest.raises(UnresolvedTargetError) as exc:
        sync(proj)
    assert str(exc.value) == (
        "target 'clade' is not defined — did you mean 'claude'? Nothing was changed. "
        "Fix `targets:` in .agentry.yml, define it under target_profiles, or install a driver "
        "overlay (`agentry target list`, then `agentry target add clade`)."
    )
    config = ConfigStore.load(proj).parsed()
    assert suggest_target("clade", config) == "claude"


def test_no_suggestion_when_nothing_close(tmp_path, local_source):
    proj = _project(tmp_path, local_source)
    _set_targets(proj, ["zzqqx"])
    with pytest.raises(UnresolvedTargetError) as exc:
        sync(proj)
    msg = str(exc.value)
    assert msg.startswith("target 'zzqqx' is not defined. Nothing was changed.")
    assert "did you mean" not in msg
    assert suggest_target("zzqqx", ConfigStore.load(proj).parsed()) is None


def test_suggestion_considers_target_profiles_keys(tmp_path, local_source):
    proj = _project(tmp_path, local_source)
    store = ConfigStore.load(proj)
    store.doc["target_profiles"] = {
        "mycli": {"skill": {"strategy": "link", "dest": ".mycli/skills/{name}"}}
    }
    store.save()
    assert suggest_target("mycl", ConfigStore.load(proj).parsed()) == "mycli"


def test_several_unresolved_targets_each_reported(tmp_path, local_source):
    proj = _project(tmp_path, local_source)
    _set_targets(proj, ["clade", "zzqqx"])
    with pytest.raises(UnresolvedTargetError) as exc:
        sync(proj)
    assert str(exc.value) == (
        "target 'clade' is not defined — did you mean 'claude'? "
        "target 'zzqqx' is not defined. Nothing was changed. Fix `targets:` in .agentry.yml, "
        "define it under target_profiles, or install a driver overlay "
        "(`agentry target list`, then `agentry target add <name>`)."
    )


def test_component_level_typo_target_fails(tmp_path, local_source):
    proj = _project(tmp_path, local_source)
    sync(proj)
    before = _snapshot(proj)
    store = ConfigStore.load(proj)
    for c in store.doc["components"]:
        if c["name"] == "github":
            c["targets"] = ["clade"]
    store.save()

    with pytest.raises(UnresolvedTargetError) as exc:
        sync(proj)
    msg = str(exc.value)
    assert msg.startswith(
        "s/mcp/github: target 'clade' is not defined — did you mean 'claude'? Nothing was changed."
    )
    assert "did you mean 'claude'?" in msg
    assert _snapshot(proj) == before


def test_fixing_typo_then_sync_succeeds(tmp_path, local_source, monkeypatch):
    proj = _project(tmp_path, local_source)
    sync(proj)
    _set_targets(proj, ["clade"])
    with pytest.raises(UnresolvedTargetError):
        sync(proj)

    _set_targets(proj, ["claude"])
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []  # nothing was removed


def test_status_exits_1_no_table_with_did_you_mean(tmp_path, local_source, monkeypatch):
    proj = _project(tmp_path, local_source)
    sync(proj)
    _set_targets(proj, ["clade"])
    with pytest.raises(UnresolvedTargetError, match="did you mean 'claude'"):
        status(proj)
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "did you mean 'claude'" in " ".join((result.output + result.stderr).split())
    assert "┃" not in result.output  # no table printed


def test_doctor_error_uses_the_suggestion(tmp_path, local_source):
    proj = _project(tmp_path, local_source)
    _set_targets(proj, ["clade"])
    errors = [c for c in run_checks(proj) if c.level == "error" and "clade" in c.message]
    assert len(errors) == 1
    msg = errors[0].message
    assert msg.startswith("target 'clade' is not defined — did you mean 'claude'?")
    assert "`agentry target add clade`" in msg
    assert describe_unresolved("clade", ConfigStore.load(proj).parsed()).startswith(
        "target 'clade' is not defined — did you mean 'claude'?"
    )


def test_component_valid_inactive_target_ok(tmp_path, local_source, monkeypatch):
    """A component listing a defined target (copilot) that the project doesn't list is not
    an unresolved-target error: only *undefined* names fail. (Whether such a target gets
    installed is a separate question, not pinned here.)"""
    proj = _project(tmp_path, local_source)
    store = ConfigStore.load(proj)
    for c in store.doc["components"]:
        c["targets"] = ["claude", "copilot"]
    store.save()
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert (proj / ".claude/skills/code-reviewer").is_symlink()


def test_catalog_overlay_not_yet_added_still_fails_without_fetching(
    tmp_path, local_source, monkeypatch
):
    """A target a catalog *could* provide is still undefined until `target add`: sync fails
    before touching any catalog, and the hint points at `target list`."""
    import json

    from agentry import registry as reg

    proj = _project(tmp_path, local_source)
    catalog = tmp_path / "overlays.json"
    catalog.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": {},
                "targets": {"myide": {"skill": {"strategy": "link", "dest": ".myide/{name}"}}},
            }
        ),
        encoding="utf-8",
    )
    store = ConfigStore.load(proj)
    store.doc["repositories"] = [{"name": "ov", "location": str(catalog)}]
    store.doc["targets"] = ["claude", "myide"]
    store.save()

    def _no_fetch(*a, **k):
        raise AssertionError("sync must not load a catalog to resolve targets")

    monkeypatch.setattr(reg, "load_catalog", _no_fetch)
    with pytest.raises(UnresolvedTargetError) as exc:
        sync(proj)
    msg = str(exc.value)
    assert "target 'myide' is not defined" in msg
    assert "`agentry target list`" in msg and "`agentry target add myide`" in msg
    assert not (proj / ".myide").exists()


def test_component_only_inactive_target_is_not_an_error(tmp_path, local_source, monkeypatch):
    """PM: `targets: [copilot]` on a component in a claude-only project is defined, so it is
    not an unresolved-target error."""
    proj = _project(tmp_path, local_source)
    store = ConfigStore.load(proj)
    for c in store.doc["components"]:
        if c["name"] == "github":
            c["targets"] = ["copilot"]
    store.save()
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert (proj / ".claude/skills/code-reviewer").is_symlink()


def test_several_components_with_typos_are_listed_in_one_prefix(tmp_path, local_source):
    proj = _project(tmp_path, local_source)
    store = ConfigStore.load(proj)
    for c in store.doc["components"]:
        c["targets"] = ["clade"]
    store.save()
    with pytest.raises(UnresolvedTargetError) as exc:
        sync(proj)
    prefix = str(exc.value).split(": target 'clade'")[0]
    assert set(prefix.split(", ")) == {"s/skill/code-reviewer", "s/mcp/github"}


# --- config-editing commands validate after their edit and save only a clean config ------


def _broken(tmp_path, local_source) -> Path:
    """An installed project whose targets: then gains a typo."""
    proj = _project(tmp_path, local_source)
    store = ConfigStore.load(proj)
    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(proj)
    store = ConfigStore.load(proj)
    store.doc["targets"].append("clade")
    store.save()
    return proj


@pytest.mark.parametrize(
    "argv",
    [["add", "s/command/deploy"], ["enable", "s/mcp/github"], ["target", "use", "cursor"]],
    ids=["add", "enable", "target-use"],
)
def test_unrelated_edit_on_broken_config_is_refused_and_writes_nothing(
    tmp_path, local_source, monkeypatch, argv
):
    proj = _broken(tmp_path, local_source)
    cfg_before = (proj / ".agentry.yml").read_bytes()
    before = _snapshot(proj)

    monkeypatch.chdir(proj)
    result = runner.invoke(app, argv)
    assert result.exit_code == 1, result.output
    assert "did you mean 'claude'" in " ".join((result.output + result.stderr).split())
    assert (proj / ".agentry.yml").read_bytes() == cfg_before  # edit NOT persisted
    assert _snapshot(proj) == before


def test_target_use_typo_suggests_the_builtin(tmp_path, local_source, monkeypatch):
    proj = _project(tmp_path, local_source)
    cfg_before = (proj / ".agentry.yml").read_bytes()
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["target", "use", "cursr"])
    assert result.exit_code == 1
    assert "did you mean 'cursor'" in " ".join((result.output + result.stderr).split())
    assert (proj / ".agentry.yml").read_bytes() == cfg_before


def test_removing_the_component_with_the_typo_fixes_it(tmp_path, local_source, monkeypatch):
    """Positive control: an edit that *repairs* the config is saved and syncs."""
    proj = _project(tmp_path, local_source)
    sync(proj)
    store = ConfigStore.load(proj)
    for c in store.doc["components"]:
        if c["name"] == "github":
            c["targets"] = ["clade"]
    store.save()

    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["remove", "s/mcp/github"])
    assert result.exit_code == 0, result.output
    assert "s/mcp/github" not in (proj / ".agentry.yml").read_text(encoding="utf-8")
    assert runner.invoke(app, ["sync"]).exit_code == 0


def test_target_add_defining_the_missing_target_fixes_it(tmp_path, local_source, monkeypatch):
    """Positive control: `target add` that defines the undefined target is saved and syncs."""
    import json

    proj = _project(tmp_path, local_source)
    catalog = tmp_path / "overlays.json"
    catalog.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": {},
                "targets": {"myide": {"skill": {"strategy": "link", "dest": ".myide/{name}"}}},
            }
        ),
        encoding="utf-8",
    )
    store = ConfigStore.load(proj)
    store.doc["repositories"] = [{"name": "ov", "location": str(catalog)}]
    store.doc["targets"] = ["claude", "myide"]
    store.save()

    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["target", "add", "myide", "--yes"])
    assert result.exit_code == 0, result.output
    assert "myide" in ConfigStore.load(proj).parsed().target_profiles
    assert (proj / ".myide/code-reviewer").is_symlink()


def test_init_with_typo_target_exits_1_and_creates_no_config(tmp_path, monkeypatch):
    proj = tmp_path / "fresh"
    proj.mkdir()
    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["init", "--target", "clade", "--no-default-catalog"])
    assert result.exit_code == 1
    assert "did you mean 'claude'" in " ".join((result.output + result.stderr).split())
    assert not (proj / ".agentry.yml").exists()
