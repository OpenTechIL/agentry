"""O1 (PM ruling "O1 runtime-confirmed"): a Claude-shaped MCP entry is SKIPPED on opencode.

OpenCode rejects its whole config when one `mcp` entry isn't `{type: local|remote, command:
[...]}`, so writing it as-is takes the project down. The opencode `mcp` rule's mismatch action
is now `skip` (data on the rule); every other rule still only warns. An `expect` override
restores warn-and-install, a `-opencode` variant installs, agentry-owned broken entries from
before are removed, user-edited ones are kept (ADR-001 W4).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync

_C = ComponentType
_CLAUDE_SHAPED = {"fs": {"command": "npx", "args": ["-y", "fs"]}}
_OC_SHAPED = {"fs": {"type": "local", "command": ["npx", "-y", "fs"]}}
_WARN_OFF = {  # a user override: the opencode mcp rule without its built-in expectation
    "opencode": {
        "mcp": {"strategy": "merge", "file": "opencode.json", "pointer": "mcp", "expect": {}}
    }
}


_HEAD = (
    "{ref}: this MCP server uses Claude's format, which OpenCode rejects; it would make "
    "OpenCode refuse the whole project config — skipped on opencode. "
)
_TAIL = "or override mcp for opencode under target_profiles. (entries: {keys})"


def _skip_line(ref: str = "s/mcp/fs", variant: str = "mcp/fs-opencode.json", keys="fs") -> str:
    """The exact warning when the variant's real path can be named."""
    return (
        _HEAD.format(ref=ref)
        + f"Ship {variant} (an OpenCode-specific variant of this file), "
        + _TAIL.format(keys=keys)
    )


def _skip_line_no_path(ref: str, keys: str) -> str:
    """The exact warning when no working variant path exists (explicit `path:`, descriptor)."""
    return (
        _HEAD.format(ref=ref)
        + "Override mcp for opencode under target_profiles. "
        + f"(entries: {keys})"
    )


def _project(tmp_path: Path, targets, fragments: dict[str, dict], profiles=None) -> Path:
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    for name, body in fragments.items():
        (src / "mcp" / f"{name}.json").write_bytes(json.dumps(body).encode("utf-8"))
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, list(targets))
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for name in fragments:
        store.add_component(Component(source="s", type=_C.MCP, name=name))
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


def _oc_mcp(proj: Path) -> dict:
    p = proj / "opencode.json"
    return json.loads(p.read_text("utf-8")).get("mcp", {}) if p.is_file() else {}


# --- skipped on opencode -------------------------------------------------------------------


def test_claude_shaped_mcp_skipped_on_opencode_with_exact_warning(tmp_path: Path):
    proj = _project(tmp_path, ["opencode"], {"fs": _CLAUDE_SHAPED})
    res = sync(proj)
    assert res.warnings.count(_skip_line()) == 1
    assert not (proj / "opencode.json").exists()  # nothing written, so nothing to reject
    assert not [w for w in res.warnings if "may not work in opencode" in w]  # not the old line


def test_skip_keeps_users_existing_opencode_json_byte_identical(tmp_path: Path):
    proj = _project(tmp_path, ["opencode"], {"fs": _CLAUDE_SHAPED})
    user = b'{"$schema": "https://opencode.ai/config.json", "theme": "mine"}\n'
    (proj / "opencode.json").write_bytes(user)
    sync(proj)
    assert (proj / "opencode.json").read_bytes() == user


def test_same_mcp_on_claude_still_installs(tmp_path: Path):
    """Guard: the skip is opencode's rule only."""
    proj = _project(tmp_path, ["claude"], {"fs": _CLAUDE_SHAPED})
    res = sync(proj)
    assert json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"] == _CLAUDE_SHAPED
    assert _skip_line() not in res.warnings


def test_mixed_targets_install_on_claude_skip_on_opencode(tmp_path: Path):
    proj = _project(tmp_path, ["claude", "opencode"], {"fs": _CLAUDE_SHAPED})
    res = sync(proj)
    assert "fs" in json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"]
    assert _oc_mcp(proj) == {}
    assert res.warnings.count(_skip_line()) == 1


def test_opencode_shaped_mcp_installs(tmp_path: Path):
    """Guard: a conforming `{type: local, command: [...]}` entry installs, silently."""
    proj = _project(tmp_path, ["opencode"], {"fs": _OC_SHAPED})
    res = sync(proj)
    assert _oc_mcp(proj) == _OC_SHAPED
    assert not [w for w in res.warnings if "opencode" in w.lower() and "s/mcp/fs" in w]


def test_opencode_variant_installs_canonical_skipped_without_warning(tmp_path: Path):
    """Guard (T16, holds today). The remedy the warning names: `fs-opencode.json` installs on opencode; the canonical
    Claude-shaped `fs` still installs on claude and is never even considered on opencode."""
    proj = _project(
        tmp_path,
        ["claude", "opencode"],
        {"fs": _CLAUDE_SHAPED, "fs-opencode": _OC_SHAPED},
    )
    res = sync(proj)
    assert _oc_mcp(proj) == _OC_SHAPED
    assert json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"] == _CLAUDE_SHAPED
    assert _skip_line() not in res.warnings


def test_expect_override_restores_warn_and_install(tmp_path: Path):
    """Guard (holds today): a user `expect: {}` override drops the rule, so the entry is
    written as-is — the user owns that choice."""
    proj = _project(tmp_path, ["opencode"], {"fs": _CLAUDE_SHAPED}, profiles=_WARN_OFF)
    res = sync(proj)
    assert _oc_mcp(proj) == _CLAUDE_SHAPED
    assert _skip_line() not in res.warnings


# --- reporting -----------------------------------------------------------------------------


def test_status_missing_with_note_and_doctor_warns(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, ["opencode"], {"fs": _CLAUDE_SHAPED})
    sync(proj)
    (row,) = [r for r in status(proj)[0] if r.ref == "s/mcp/fs" and r.target == "opencode"]
    assert row.state == "missing"
    assert row.where == "opencode.json:mcp"
    assert row.note == "skipped on opencode: fs uses a format opencode rejects (see warnings)"

    checks = [(c.level, c.category) for c in run_checks(proj) if c.message == _skip_line()]
    assert checks == [("warn", "format")]
    monkeypatch.chdir(proj)
    env = {"COLUMNS": "400", "PATH": "/usr/bin:/bin"}  # no `agy` alias warning in the way
    assert CliRunner(env=env).invoke(app, ["doctor", "--strict"]).exit_code == 1


# --- upgrade: entries written before the change ------------------------------------------


def test_existing_agentry_owned_broken_entry_removed_on_next_sync(tmp_path: Path):
    """Installed as-is by an older agentry (simulated with a warn-only override); once the
    built-in skip rule applies, the next sync removes agentry's entry and opencode recovers."""
    proj = _project(tmp_path, ["opencode"], {"fs": _CLAUDE_SHAPED}, profiles=_WARN_OFF)
    (proj / "opencode.json").write_bytes(b'{"theme": "mine"}\n')
    sync(proj)
    assert _oc_mcp(proj) == _CLAUDE_SHAPED
    _set_profiles(proj, None)

    res = sync(proj)
    doc = json.loads((proj / "opencode.json").read_text("utf-8"))
    assert "fs" not in doc.get("mcp", {})
    assert doc["theme"] == "mine"  # the user's content stays
    assert res.warnings.count(_skip_line()) == 1


def test_existing_agentry_created_file_deleted_when_only_broken_entry(tmp_path: Path):
    """#45 composes: if agentry created opencode.json just for this entry, it goes too."""
    proj = _project(tmp_path, ["opencode"], {"fs": _CLAUDE_SHAPED}, profiles=_WARN_OFF)
    sync(proj)
    _set_profiles(proj, None)
    sync(proj)
    assert not (proj / "opencode.json").exists()


def test_user_edited_broken_entry_is_kept(tmp_path: Path):
    """ADR-001 W4: an entry the user edited after agentry wrote it is theirs — kept, even
    though the rule now skips agentry's version."""
    proj = _project(tmp_path, ["opencode"], {"fs": _CLAUDE_SHAPED}, profiles=_WARN_OFF)
    sync(proj)
    doc = json.loads((proj / "opencode.json").read_text("utf-8"))
    doc["mcp"]["fs"]["args"] = ["-y", "fs", "--mine"]
    (proj / "opencode.json").write_bytes(json.dumps(doc).encode("utf-8"))
    _set_profiles(proj, None)

    res = sync(proj)
    assert _oc_mcp(proj)["fs"]["args"] == ["-y", "fs", "--mine"]
    assert res.warnings.count(_skip_line()) == 1


# --- stability and scope -----------------------------------------------------------------


def test_skip_is_idempotent(tmp_path: Path):
    proj = _project(tmp_path, ["claude", "opencode"], {"fs": _CLAUDE_SHAPED})
    first = sync(proj)
    second = sync(proj)
    assert second.created == [] and second.updated == [] and second.removed == []
    assert sorted(second.warnings) == sorted(first.warnings)
    assert not (proj / "opencode.json").exists()


def test_gemini_rules_still_warn_and_install_as_is(tmp_path: Path):
    """Guard: only the opencode mcp rule skips. Gemini's mcp rule (http `type`) and its hook
    rule (Claude event names) still warn and write the bytes as-is."""
    src = tmp_path / "src"
    (src / "mcp").mkdir(parents=True)
    http = {"web": {"type": "http", "url": "https://example.invalid/mcp"}}
    (src / "mcp" / "web.json").write_bytes(json.dumps(http).encode("utf-8"))
    (src / "hooks").mkdir()
    hook = {"PreToolUse": [{"hooks": [{"type": "command", "command": "fmt"}]}]}
    (src / "hooks" / "fmt.json").write_bytes(json.dumps(hook).encode("utf-8"))
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["gemini"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.MCP, name="web"))
    store.add_component(Component(source="s", type=_C.HOOK, name="fmt"))
    store.save()

    res = sync(proj)
    settings = json.loads((proj / ".gemini/settings.json").read_text("utf-8"))
    assert settings["mcpServers"]["web"] == http["web"]
    assert "PreToolUse" in settings["hooks"]
    assert any(w.startswith("s/mcp/web: may not work in gemini") for w in res.warnings)
    assert any(w.startswith("s/hook/fmt: may not work in gemini") for w in res.warnings)
    assert not [w for w in res.warnings if "skipped on" in w]


# --- the variant path the warning names (PM ruling) ---------------------------------------


def _prove_variant_installs(proj: Path, path: Path, component: str) -> None:
    """Follow the printed hint literally: create the file, enable it, sync — it installs and
    the skip warning is gone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(_OC_SHAPED).encode())
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.MCP, name=component))
    store.save()
    res = sync(proj)
    assert _oc_mcp(proj) == _OC_SHAPED
    assert not [w for w in res.warnings if "skipped on opencode" in w]


def test_warning_names_the_real_variant_path_and_all_entries(tmp_path: Path):
    """`mcp/github.json` with two Claude-shaped servers: the whole component is skipped, the
    warning names `mcp/github-opencode.json`, lists both keys in fragment order, and the status
    note uses the plural."""
    two = {"gh": {"command": "npx", "args": ["gh"]}, "gl": {"command": "npx", "args": ["gl"]}}
    proj = _project(tmp_path, ["opencode"], {"github": two})
    res = sync(proj)
    want = _skip_line("s/mcp/github", "mcp/github-opencode.json", "gh, gl")
    assert res.warnings.count(want) == 1
    (row,) = [r for r in status(proj)[0] if r.ref == "s/mcp/github"]
    assert row.note == "skipped on opencode: gh, gl use a format opencode rejects (see warnings)"


def test_one_bad_entry_skips_the_whole_component(tmp_path: Path):
    mixed = {"ok": {"type": "local", "command": ["npx", "ok"]}, "bad": {"command": "npx"}}
    proj = _project(tmp_path, ["opencode"], {"github": mixed})
    res = sync(proj)
    assert _oc_mcp(proj) == {}
    assert res.warnings.count(_skip_line("s/mcp/github", "mcp/github-opencode.json", "bad")) == 1


def test_variant_path_includes_the_source_subdir(tmp_path: Path):
    """With `subdir:`, the named path is relative to the source root, so it includes the
    subdir — the place the author actually has to put the file."""
    src = tmp_path / "src"
    (src / "pkg" / "mcp").mkdir(parents=True)
    (src / "pkg" / "mcp" / "github.json").write_bytes(json.dumps(_CLAUDE_SHAPED).encode())
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src), subdir="pkg"))
    store.add_component(Component(source="s", type=_C.MCP, name="github"))
    store.save()
    res = sync(proj)
    assert res.warnings.count(_skip_line("s/mcp/github", "pkg/mcp/github-opencode.json")) == 1

    # and the named path really works: putting the variant there installs it
    (src / "pkg" / "mcp" / "github-opencode.json").write_bytes(json.dumps(_OC_SHAPED).encode())
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.MCP, name="github-opencode"))
    store.save()
    res = sync(proj)
    assert _oc_mcp(proj) == _OC_SHAPED
    assert not [w for w in res.warnings if "skipped on opencode" in w]


def test_named_variant_path_is_where_the_variant_installs_from(tmp_path: Path):
    """No subdir: following the warning literally (create `mcp/fs-opencode.json`) fixes it."""
    proj = _project(tmp_path, ["opencode"], {"fs": _CLAUDE_SHAPED})
    assert _skip_line() in sync(proj).warnings
    (tmp_path / "src/mcp/fs-opencode.json").write_bytes(json.dumps(_OC_SHAPED).encode())
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.MCP, name="fs-opencode"))
    store.save()
    res = sync(proj)
    assert _oc_mcp(proj) == _OC_SHAPED
    assert not [w for w in res.warnings if "skipped on opencode" in w]


def test_root_mcp_json_names_a_working_variant_path(tmp_path: Path):
    """A plugin-style root `.mcp.json` (component `mcp`): a root-level `mcp-opencode.json`
    would NOT be picked up, so the hint names `mcp/mcp-opencode.json` — and that file really
    installs."""
    src = tmp_path / "src"
    src.mkdir()
    (src / ".mcp.json").write_bytes(json.dumps({"mcpServers": _CLAUDE_SHAPED}).encode())
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.MCP, name="mcp"))
    store.save()
    (w,) = [w for w in sync(proj).warnings if "skipped on opencode" in w]
    assert w == _skip_line("s/mcp/mcp", "mcp/mcp-opencode.json")
    _prove_variant_installs(proj, src / "mcp" / "mcp-opencode.json", "mcp-opencode")


def test_explicit_path_component_prints_no_path(tmp_path: Path):
    """An explicit `path:` places the fragment where no variant would be discovered, so the
    warning names no file at all — never a path that wouldn't work."""
    src = tmp_path / "src"
    (src / "servers").mkdir(parents=True)
    (src / "servers" / "fs.json").write_bytes(json.dumps(_CLAUDE_SHAPED).encode())
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.MCP, name="fs", path="servers/fs.json"))
    store.save()
    (w,) = [w for w in sync(proj).warnings if "skipped on opencode" in w]
    assert w == _skip_line_no_path("s/mcp/fs", "fs")
    body = w.split(":", 1)[1]  # past the ref, which itself contains "mcp/"
    assert "Ship" not in body and "mcp/" not in body and "-opencode.json" not in body


def test_descriptor_source_prints_no_path(tmp_path: Path):
    """A descriptor-placed (agentry.yaml) mcp: also no path."""
    src = tmp_path / "src"
    (src / "conf").mkdir(parents=True)
    (src / "conf" / "srv.json").write_bytes(json.dumps(_CLAUDE_SHAPED).encode())
    (src / "agentry.yaml").write_bytes(
        b"version: 1\nprovides:\n  mcp:\n    - { name: srv, path: conf/srv.json }\n"
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=_C.MCP, name="srv"))
    store.save()
    (w,) = [w for w in sync(proj).warnings if "skipped on opencode" in w]
    assert w == _skip_line_no_path("s/mcp/srv", "fs")
    body = w.split(":", 1)[1]
    assert "Ship" not in body and "mcp/" not in body and "-opencode.json" not in body


def test_inline_plugin_json_mcp_names_a_working_variant_path(tmp_path: Path):
    """MCP servers inline in plugin.json: the hint names `mcp/<name>-opencode.json`, and
    creating that file makes the variant install."""
    src = tmp_path / "src"
    (src / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": "tools", "mcpServers": _CLAUDE_SHAPED}
    (src / ".claude-plugin" / "plugin.json").write_bytes(json.dumps(manifest).encode())
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.save()
    from agentry import discovery

    (mcp,) = [d for d in discovery.discover(src) if d.type is _C.MCP]
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.MCP, name=mcp.name))
    store.save()
    (w,) = [w for w in sync(proj).warnings if "skipped on opencode" in w]
    assert w == _skip_line(f"s/mcp/{mcp.name}", f"mcp/{mcp.name}-opencode.json")
    _prove_variant_installs(proj, src / "mcp" / f"{mcp.name}-opencode.json", f"{mcp.name}-opencode")


def test_root_mcp_json_with_subdir_names_the_subdir_path(tmp_path: Path):
    """Root `.mcp.json` of a source used with `subdir:`: the hint is `pkg/mcp/mcp-opencode.json`
    (relative to the source root), and that file really installs."""
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / ".mcp.json").write_bytes(json.dumps({"mcpServers": _CLAUDE_SHAPED}).encode())
    proj = tmp_path / "proj"
    proj.mkdir()
    store = ConfigStore.create(proj, ["opencode"])
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src), subdir="pkg"))
    store.add_component(Component(source="s", type=_C.MCP, name="mcp"))
    store.save()
    (w,) = [w for w in sync(proj).warnings if "skipped on opencode" in w]
    assert w == _skip_line("s/mcp/mcp", "pkg/mcp/mcp-opencode.json")
    _prove_variant_installs(proj, src / "pkg" / "mcp" / "mcp-opencode.json", "mcp-opencode")
