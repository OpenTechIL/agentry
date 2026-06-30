"""Tests for `agy doctor` preflight checks (doctor.run_checks)."""

from __future__ import annotations

from pathlib import Path

from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.models import Component, ComponentType, Source, SourceType, Target

_C = ComponentType


def _proj(tmp_path: Path, src: Path, *comps: Component) -> tuple[Path, ConfigStore]:
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for c in comps:
        store.add_component(c)
    store.save()
    return proj, store


def _mcp_source(root: Path, fragment: str) -> Path:
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "gh.json").write_text(fragment)
    return root


def test_healthy_project_has_no_errors(tmp_path: Path, local_source: Path):
    proj, _ = _proj(
        tmp_path, local_source, Component(source="s", type=_C.SKILL, name="code-reviewer")
    )
    assert not any(c.level == "error" for c in run_checks(proj))


def test_unresolved_target_is_error(tmp_path: Path, local_source: Path):
    proj, store = _proj(tmp_path, local_source)
    store.doc["targets"].append("ghostide")
    store.save()
    checks = run_checks(proj)
    assert any(c.level == "error" and "ghostide" in c.message for c in checks)


def test_unprovided_component_is_error(tmp_path: Path, local_source: Path):
    proj, _ = _proj(
        tmp_path, local_source, Component(source="s", type=_C.SKILL, name="nonexistent")
    )
    checks = run_checks(proj)
    assert any(c.level == "error" and "not provided" in c.message for c in checks)


def test_unset_env_var_in_mcp_is_warning(tmp_path: Path):
    src = _mcp_source(
        tmp_path / "src", '{"gh": {"command": "x", "env": {"T": "${UNSET_DOCTOR_VAR}"}}}'
    )
    proj, _ = _proj(tmp_path, src, Component(source="s", type=_C.MCP, name="gh"))
    checks = run_checks(proj)
    assert any(c.category == "env" and "UNSET_DOCTOR_VAR" in c.message for c in checks)


def test_env_var_with_default_not_flagged(tmp_path: Path):
    src = _mcp_source(tmp_path / "src", '{"gh": {"command": "x", "args": ["${HOST:-localhost}"]}}')
    proj, _ = _proj(tmp_path, src, Component(source="s", type=_C.MCP, name="gh"))
    assert not any(c.category == "env" for c in run_checks(proj))


def test_set_env_var_not_flagged(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DOCTOR_SET_VAR", "present")
    src = _mcp_source(
        tmp_path / "src", '{"gh": {"command": "x", "env": {"T": "${DOCTOR_SET_VAR}"}}}'
    )
    proj, _ = _proj(tmp_path, src, Component(source="s", type=_C.MCP, name="gh"))
    assert not any(c.category == "env" for c in run_checks(proj))
