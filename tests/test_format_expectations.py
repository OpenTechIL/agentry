"""T12 (D7 phase A): cross-target format-mismatch warnings.

agentry still installs bytes as-is; it only *warns* (and `doctor --strict` fails) when an
artifact or merged entry visibly won't work in a target. Rules are data on the driver's
TargetSpec (`expect`) or a `target_profiles` rule — these tests pin each built-in rule both
ways, the reporting surface, and that nothing on disk changes because of a rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.doctor import run_checks
from agentry.drivers.gemini import GEMINI_HOOK_EVENTS
from agentry.models import (
    Component,
    ComponentTransform,
    ComponentType,
    Source,
    SourceType,
    Target,
)
from agentry.reconcile import sync
from agentry.spec import FormatExpectation, check_format

_C = ComponentType
runner = CliRunner()

_O1 = FormatExpectation(
    require=("type",),
    types={"command": "array"},
    forbid=("args", "env"),
    values={"type": ("local", "remote")},
)


# --- check_format: pure unit tests, one positive + one negative per rule ----------------


def test_o1_compliant_opencode_entry_has_no_problems():
    entries = {"fs": {"type": "local", "command": ["npx", "fs"], "environment": {}}}
    assert check_format(_O1, entries=entries) == []


@pytest.mark.parametrize(
    "entry, fragment",
    [
        ({"command": ["npx"]}, "'fs' has no 'type'"),
        ({"type": "local", "command": "npx"}, "'fs'.command is a string, not an array"),
        ({"type": "local", "command": ["npx"], "args": []}, "'fs' uses 'args'"),
        ({"type": "local", "command": ["npx"], "env": {}}, "'fs' uses 'env'"),
        ({"type": "http", "command": ["npx"]}, "'fs'.type is 'http', not one of local, remote"),
    ],
    ids=["no-type", "string-command", "args", "env", "bad-type"],
)
def test_o1_each_violation_is_reported(entry, fragment):
    problems = check_format(_O1, entries={"fs": entry})
    assert any(fragment in p for p in problems), problems


def test_g1_extension_rule_flags_only_wrong_suffix_files(tmp_path: Path):
    exp = FormatExpectation(extension=".toml")
    md = tmp_path / "ship.md"
    md.write_text("x", encoding="utf-8")
    toml = tmp_path / "ship.toml"
    toml.write_text('prompt = "x"\n', encoding="utf-8")
    assert any("'ship.md' is not a .toml file" in p for p in check_format(exp, artifact=md))
    assert check_format(exp, artifact=toml) == []


def test_g1_directory_artifact_is_skipped(tmp_path: Path):
    d = tmp_path / "ship"
    d.mkdir()
    assert check_format(FormatExpectation(extension=".toml"), artifact=d) == []


def test_g2_event_rule_flags_unknown_events_only():
    exp = FormatExpectation(events=GEMINI_HOOK_EVENTS)
    assert check_format(exp, entries={"AfterTool": [], "SessionStart": []}) == []
    problems = check_format(exp, entries={"AfterTool": [], "PostToolUse": [], "Stop": []})
    assert any("unknown event(s)" in p and "PostToolUse" in p and "Stop" in p for p in problems)
    assert not any("AfterTool" in p for p in problems)


def test_gemini_hook_events_set_has_the_eleven_documented_names():
    assert len(GEMINI_HOOK_EVENTS) == 11
    assert {"BeforeTool", "AfterTool", "SessionStart"} <= GEMINI_HOOK_EVENTS
    assert "PreToolUse" not in GEMINI_HOOK_EVENTS


# --- end to end through sync ------------------------------------------------------------


def _source(root: Path) -> Path:
    """Claude-shaped content: a Markdown command, a Claude MCP entry, a Claude hook event."""
    (root / "commands").mkdir(parents=True)
    (root / "commands" / "ship.md").write_text("# ship\n", encoding="utf-8")
    (root / "mcp").mkdir()
    (root / "mcp" / "fs.json").write_text(
        json.dumps({"fs": {"command": "npx", "args": ["-y", "fs"]}}), encoding="utf-8"
    )
    (root / "hooks").mkdir()
    (root / "hooks" / "fmt.json").write_text(
        json.dumps(
            {"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "fmt"}]}]}}
        ),
        encoding="utf-8",
    )
    return root


def _project(tmp_path: Path, targets: list[Target], *comps: tuple[ComponentType, str]) -> Path:
    src = _source(tmp_path / "src")
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, targets).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    for ctype, name in comps:
        store.add_component(Component(source="s", type=ctype, name=name))
    store.save()
    return proj


def _fmt(warnings: list[str], target: str) -> list[str]:
    return [w for w in warnings if f": may not work in {target}" in w]


def test_o1_claude_shaped_mcp_warns_for_opencode_and_installs_as_is(tmp_path: Path):
    proj = _project(tmp_path, [Target.OPENCODE], (_C.MCP, "fs"))
    res = sync(proj)
    (w,) = _fmt(res.warnings, "opencode")
    assert w.startswith("s/mcp/fs: may not work in opencode")
    assert "'fs' has no 'type'" in w and "'fs'.command is a string, not an array" in w
    assert "'fs' uses 'args'" in w
    assert "installed as-is" in w and "target_profiles" in w
    # installed byte-for-byte as the source wrote it
    assert json.loads((proj / "opencode.json").read_text(encoding="utf-8"))["mcp"]["fs"] == {
        "command": "npx",
        "args": ["-y", "fs"],
    }


def test_o1_compliant_mcp_is_silent_for_opencode(tmp_path: Path):
    proj = _project(tmp_path, [Target.OPENCODE], (_C.MCP, "fs"))
    (tmp_path / "src" / "mcp" / "fs.json").write_text(
        json.dumps({"fs": {"type": "local", "command": ["npx", "-y", "fs"]}}), encoding="utf-8"
    )
    assert _fmt(sync(proj).warnings, "opencode") == []


def test_g1_markdown_command_warns_for_gemini(tmp_path: Path):
    proj = _project(tmp_path, [Target.GEMINI], (_C.COMMAND, "ship"))
    (w,) = _fmt(sync(proj).warnings, "gemini")
    assert "'ship.md' is not a .toml file" in w
    assert (proj / ".gemini/commands/ship.toml").is_symlink()  # still installed


def test_g2_claude_hook_event_warns_for_gemini(tmp_path: Path):
    proj = _project(tmp_path, [Target.GEMINI], (_C.HOOK, "fmt"))
    res = sync(proj)
    (w,) = _fmt(res.warnings, "gemini")
    assert "unknown event(s) PostToolUse" in w
    hooks = json.loads((proj / ".gemini/settings.json").read_text(encoding="utf-8"))["hooks"]
    assert "PostToolUse" in hooks  # phase A: warned, not dropped


def test_g2_gemini_native_hook_event_is_silent(tmp_path: Path):
    proj = _project(tmp_path, [Target.GEMINI], (_C.HOOK, "fmt"))
    (tmp_path / "src" / "hooks" / "fmt.json").write_text(
        json.dumps({"hooks": {"AfterTool": [{"hooks": [{"type": "command", "command": "f"}]}]}}),
        encoding="utf-8",
    )
    assert _fmt(sync(proj).warnings, "gemini") == []


def test_claude_has_no_format_rules(tmp_path: Path):
    proj = _project(
        tmp_path, [Target.CLAUDE], (_C.MCP, "fs"), (_C.HOOK, "fmt"), (_C.COMMAND, "ship")
    )
    assert not any("may not work in" in w for w in sync(proj).warnings)


def test_one_warning_per_component_and_target(tmp_path: Path):
    proj = _project(
        tmp_path,
        [Target.CLAUDE, Target.GEMINI, Target.OPENCODE],
        (_C.MCP, "fs"),
        (_C.HOOK, "fmt"),
        (_C.COMMAND, "ship"),
    )
    res = sync(proj)
    pairs = [
        (w.split(":")[0], w.split("may not work in ")[1].split()[0])
        for w in res.warnings
        if "may not work in" in w
    ]
    assert len(pairs) == len(set(pairs))
    assert ("s/command/ship", "gemini") in pairs
    assert ("s/hook/fmt", "gemini") in pairs
    assert ("s/mcp/fs", "opencode") in pairs


def test_format_rules_change_no_bytes_on_disk(tmp_path: Path):
    """The same project synced with the built-in rule and with it dropped (a profile override
    with `expect: {}`) writes identical files: a rule only ever warns."""
    with_rule = _project(tmp_path / "a", [Target.OPENCODE], (_C.MCP, "fs"))
    assert _fmt(sync(with_rule).warnings, "opencode")

    without = _project(tmp_path / "b", [Target.OPENCODE], (_C.MCP, "fs"))
    _set_profiles(
        without,
        {
            "opencode": {
                "mcp": {
                    "strategy": "merge",
                    "file": "opencode.json",
                    "pointer": "mcp",
                    "expect": {},
                }
            }
        },
    )
    assert _fmt(sync(without).warnings, "opencode") == []  # rule really dropped
    assert (with_rule / "opencode.json").read_bytes() == (without / "opencode.json").read_bytes()


def test_format_warnings_do_not_break_idempotency(tmp_path: Path):
    proj = _project(tmp_path, [Target.GEMINI, Target.OPENCODE], (_C.MCP, "fs"), (_C.HOOK, "fmt"))
    sync(proj)
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert _fmt(res.warnings, "opencode")  # still reported every sync


# --- doctor ----------------------------------------------------------------------------


def test_doctor_reports_format_warnings_and_strict_fails(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, [Target.GEMINI, Target.OPENCODE], (_C.MCP, "fs"), (_C.HOOK, "fmt"))
    sync(proj)
    fmt = [c for c in run_checks(proj) if c.category == "format"]
    assert fmt and all(c.level == "warn" for c in fmt)
    assert any("s/mcp/fs: may not work in opencode" in c.message for c in fmt)
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["doctor", "--strict"]).exit_code == 1


def test_doctor_has_no_format_checks_for_compliant_project(tmp_path: Path):
    proj = _project(tmp_path, [Target.CLAUDE], (_C.MCP, "fs"))
    sync(proj)
    assert not [c for c in run_checks(proj) if c.category == "format"]


# --- target_profiles overrides ----------------------------------------------------------


def _set_profiles(proj: Path, profiles: dict) -> None:
    """Write raw target_profiles (as a user would in .agentry.yml)."""
    store = ConfigStore.load(proj)
    store.doc["target_profiles"] = profiles
    store.save()


def test_profile_empty_expect_drops_the_builtin_rule(tmp_path: Path):
    """PM: `expect: {}` on a profile rule drops the built-in check."""
    proj = _project(tmp_path, [Target.GEMINI], (_C.COMMAND, "ship"))
    _set_profiles(
        proj,
        {
            "gemini": {
                "command": {"strategy": "link", "dest": ".gemini/commands/{name}.md", "expect": {}}
            }
        },
    )
    assert _fmt(sync(proj).warnings, "gemini") == []


def test_profile_override_without_expect_keeps_the_builtin_rule(tmp_path: Path):
    """PM: a profile rule that omits `expect:` keeps the built-in check for that type."""
    proj = _project(tmp_path, [Target.GEMINI], (_C.COMMAND, "ship"))
    _set_profiles(
        proj, {"gemini": {"command": {"strategy": "link", "dest": ".gemini/commands/{name}.toml"}}}
    )
    (w,) = _fmt(sync(proj).warnings, "gemini")
    assert "'ship.md' is not a .toml file" in w


def test_profile_expect_replaces_the_builtin_rule(tmp_path: Path):
    """`expect:` on a profile rule replaces (not extends) the built-in one."""
    proj = _project(tmp_path, [Target.OPENCODE], (_C.MCP, "fs"))
    _set_profiles(
        proj,
        {
            "opencode": {
                "mcp": {
                    "strategy": "merge",
                    "file": "opencode.json",
                    "pointer": "mcp",
                    "expect": {"forbid": ["args"]},
                }
            }
        },
    )
    (w,) = _fmt(sync(proj).warnings, "opencode")
    assert "'fs' uses 'args'" in w
    assert "has no 'type'" not in w and "not an array" not in w  # built-in rule gone


def test_link_merge_is_checked_after_the_path_rewrite(tmp_path: Path):
    """A link+merge fragment is checked as it will land (after rewrite_from → rewrite_to),
    so a rule satisfied only by the rewritten command is quiet."""
    src = tmp_path / "src"
    (src / "srv").mkdir(parents=True)
    (src / "srv" / "run.sh").write_text("echo\n", encoding="utf-8")
    (src / "srv" / "hooks.json").write_text(
        json.dumps(
            {
                "fs": {
                    "type": "local",
                    "command": ["${PLUGIN_ROOT}/run.sh"],
                    "cwd": "${PLUGIN_ROOT}",
                }
            }
        ),
        encoding="utf-8",
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    rewritten = "${PROJECT}/.opencode/srv/srv/run.sh"
    (proj / ".agentry.yml").write_text(
        f"""version: 1
targets: [opencode]
sources:
  - name: s
    type: local
    path: {src}
components:
  - source: s
    type: mcp
    name: srv
    path: srv
target_profiles:
  opencode:
    mcp:
      strategy: link+merge
      dest: ".opencode/srv/{{name}}"
      file: opencode.json
      pointer: mcp
      rewrite_from: "${{PLUGIN_ROOT}}"
      rewrite_to: "${{PROJECT}}/.opencode/srv/{{name}}"
      expect:
        values:
          cwd: ["${{PROJECT}}/.opencode/srv/srv"]
""",
        encoding="utf-8",
    )
    res = sync(proj)
    merged = json.loads((proj / "opencode.json").read_text(encoding="utf-8"))["mcp"]["fs"]
    assert merged["command"] == [rewritten]
    assert merged["cwd"] == "${PROJECT}/.opencode/srv/srv"
    # Pre-rewrite the cwd is "${PLUGIN_ROOT}", which the rule rejects: silence proves the
    # check ran on the rewritten fragment.
    assert _fmt(res.warnings, "opencode") == []

    # Control: the same profile with a rule the *rewritten* fragment violates does warn.
    cfg = proj / ".agentry.yml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            "values:", "require: [environment]\n        values:"
        ),
        encoding="utf-8",
    )
    (w,) = _fmt(sync(proj).warnings, "opencode")
    assert "'fs' has no 'environment'" in w


# --- G3: gemini rejects remote (http) MCP entries -------------------------------------------------------------


def test_g3_gemini_http_mcp_warns(tmp_path: Path):
    proj = _project(tmp_path, [Target.GEMINI], (_C.MCP, "fs"))
    (tmp_path / "src" / "mcp" / "fs.json").write_text(
        json.dumps({"fs": {"type": "http", "url": "https://example.invalid/mcp"}}),
        encoding="utf-8",
    )
    (w,) = _fmt(sync(proj).warnings, "gemini")
    assert "'fs'.type is 'http'" in w and "use `httpUrl` for streamable HTTP" in w


def test_g3_gemini_native_http_mcp_is_silent(tmp_path: Path):
    proj = _project(tmp_path, [Target.GEMINI], (_C.MCP, "fs"))
    (tmp_path / "src" / "mcp" / "fs.json").write_text(
        json.dumps({"fs": {"httpUrl": "https://example.invalid/mcp"}}), encoding="utf-8"
    )
    assert _fmt(sync(proj).warnings, "gemini") == []


# --- PM rulings: format summary and transformed output ---------------------------------------------

FORMAT_SUMMARY = (
    "  ! 2 components may not work on their target (format mismatch) — run "
    "`agentry doctor` for the list"
)


def _warn_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if ln.startswith("  ! ")]


def test_format_warnings_summarize_and_verbose_lists_all(tmp_path: Path, monkeypatch):
    one = _project(tmp_path / "one", [Target.GEMINI], (_C.COMMAND, "ship"))
    (single,) = _fmt(sync(one).warnings, "gemini")
    monkeypatch.chdir(one)
    lines = _warn_lines(runner.invoke(app, ["sync"]).stderr)
    assert f"  ! {single}" in lines and FORMAT_SUMMARY not in lines

    two = _project(tmp_path / "two", [Target.GEMINI], (_C.COMMAND, "ship"), (_C.HOOK, "fmt"))
    full = _fmt(sync(two).warnings, "gemini")
    assert len(full) == 2
    monkeypatch.chdir(two)
    lines = _warn_lines(runner.invoke(app, ["sync"]).stderr)
    assert lines.count(FORMAT_SUMMARY) == 1
    assert not any(f"  ! {w}" in lines for w in full)
    verbose = _warn_lines(runner.invoke(app, ["sync", "--verbose"]).stderr)
    assert all(f"  ! {w}" in verbose for w in full) and FORMAT_SUMMARY not in verbose


def test_transformed_command_is_not_flagged_by_g1(tmp_path: Path):
    """A `transform:` component's installed file is the transform's output, so the source's
    .md extension must not trigger the G1 .toml warning."""
    proj = tmp_path / "proj"
    _source(tmp_path / "src")
    proj.mkdir()
    ConfigStore.create(proj, [Target.GEMINI]).save()
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(tmp_path / "src")))
    store.add_component(
        Component(
            source="s",
            type=_C.COMMAND,
            name="ship",
            transform=ComponentTransform(provider="strip-frontmatter"),
        )
    )
    store.save()
    assert _fmt(sync(proj).warnings, "gemini") == []


# --- reject / reject_hint ----------------------------------------------------------------


def test_reject_rule_flags_listed_values_with_hint():
    exp = FormatExpectation(reject={"type": ("http", "streamable-http")}, reject_hint="use X")
    assert check_format(exp, entries={"a": {"type": "local"}}) == []
    for bad in ("http", "streamable-http"):
        (p,) = check_format(exp, entries={"a": {"type": bad}})
        assert f"'a'.type is '{bad}'" in p and p.endswith(" — use X")
    (p,) = check_format(
        FormatExpectation(reject={"type": ("http",)}), entries={"a": {"type": "http"}}
    )
    assert p == "'a'.type is 'http'"  # no hint, no dangling separator


def test_gemini_rejects_streamable_http_type_too(tmp_path: Path):
    proj = _project(tmp_path, [Target.GEMINI], (_C.MCP, "fs"))
    (tmp_path / "src" / "mcp" / "fs.json").write_text(
        json.dumps({"fs": {"type": "streamable-http", "url": "https://example.invalid/mcp"}}),
        encoding="utf-8",
    )
    (w,) = _fmt(sync(proj).warnings, "gemini")
    assert "'fs'.type is 'streamable-http'" in w


def test_profile_expect_reject_and_hint_replace_the_builtin(tmp_path: Path):
    """A profile `expect:` can declare `reject` + `reject_hint`; it replaces the built-in
    rule (O1's require/forbid no longer fire)."""
    proj = _project(tmp_path, [Target.OPENCODE], (_C.MCP, "fs"))
    (tmp_path / "src" / "mcp" / "fs.json").write_text(
        json.dumps({"fs": {"command": "npx", "args": [], "type": "sse"}}), encoding="utf-8"
    )
    _set_profiles(
        proj,
        {
            "opencode": {
                "mcp": {
                    "strategy": "merge",
                    "file": "opencode.json",
                    "pointer": "mcp",
                    "expect": {"reject": {"type": ["sse"]}, "reject_hint": "use remote"},
                }
            }
        },
    )
    (w,) = _fmt(sync(proj).warnings, "opencode")
    assert "'fs'.type is 'sse' — use remote" in w
    assert "has no 'type'" not in w and "uses 'args'" not in w


# --- the format summary counts distinct components, not (component, target) pairs -------


def _summary(n: int) -> str:
    noun = "component" if n == 1 else "components"
    return (
        f"  ! {n} {noun} may not work on their target (format mismatch) — run "
        "`agentry doctor` for the list"
    )


def _add_web_mcp(tmp_path: Path, proj: Path) -> None:
    """An http MCP entry: fails O1 on opencode *and* G3 on gemini (two pairs, one ref)."""
    (tmp_path / "src" / "mcp" / "web.json").write_text(
        json.dumps({"web": {"type": "http", "url": "https://example.invalid/mcp"}}),
        encoding="utf-8",
    )
    store = ConfigStore.load(proj)
    store.add_component(Component(source="s", type=_C.MCP, name="web"))
    store.save()


def test_format_summary_counts_components_not_pairs(tmp_path: Path, monkeypatch):
    proj = _project(
        tmp_path,
        [Target.GEMINI, Target.OPENCODE],
        (_C.MCP, "fs"),
        (_C.COMMAND, "ship"),
        (_C.HOOK, "fmt"),
    )
    _add_web_mcp(tmp_path, proj)
    res = sync(proj)
    pairs = [w for w in res.warnings if "may not work in" in w]
    assert len(pairs) == 5 and len({w.split(":")[0] for w in pairs}) == 4
    assert sorted(res.summarized["format"]) == sorted(pairs)  # one message per pair

    monkeypatch.chdir(proj)
    lines = _warn_lines(runner.invoke(app, ["sync"]).stderr)
    assert lines.count(_summary(4)) == 1
    verbose = _warn_lines(runner.invoke(app, ["sync", "--verbose"]).stderr)
    assert all(f"  ! {w}" in verbose for w in pairs)


def test_format_summary_singular_for_one_component_on_two_targets(tmp_path: Path, monkeypatch):
    proj = _project(tmp_path, [Target.GEMINI, Target.OPENCODE])
    _add_web_mcp(tmp_path, proj)
    pairs = [w for w in sync(proj).warnings if "may not work in" in w]
    assert len(pairs) == 2

    monkeypatch.chdir(proj)
    lines = _warn_lines(runner.invoke(app, ["sync"]).stderr)
    assert lines.count(_summary(1)) == 1
    assert not any(f"  ! {w}" in lines for w in pairs)
