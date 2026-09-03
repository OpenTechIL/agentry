"""Direct tests for `envscan` and the `Driver` abstraction.

`envscan.unset_env_refs` gates install-time warnings from `reconcile` and `doctor`, and
`drivers.base.Driver` is the capability map every strategy decision routes through. Both
were only reached transitively, so their contracts weren't pinned anywhere.
"""

from __future__ import annotations

import pytest

from agentry.drivers import BUILTIN_DRIVERS, resolve_drivers
from agentry.drivers.base import Driver
from agentry.envscan import unset_env_refs
from agentry.models import ComponentType, Config, ProfileRule, Strategy
from agentry.spec import TargetSpec

# -- envscan --------------------------------------------------------------


def test_unset_refs_are_reported(monkeypatch):
    monkeypatch.delenv("AGENTRY_TEST_TOKEN", raising=False)
    assert unset_env_refs('{"k": "${AGENTRY_TEST_TOKEN}"}') == ["AGENTRY_TEST_TOKEN"]


def test_set_refs_are_not_reported(monkeypatch):
    monkeypatch.setenv("AGENTRY_TEST_TOKEN", "x")
    assert unset_env_refs('{"k": "${AGENTRY_TEST_TOKEN}"}') == []


def test_plugin_root_vars_are_never_reported(monkeypatch):
    """Host-injected by the harness, so "unset" locally says nothing.

    Both the bare name and any harness prefix must be skipped — the plain-merge path warns
    about these separately and more accurately (see knowledge-base, 2026-07-09).
    """
    for var in ("CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT", "SOME_OTHER_PLUGIN_ROOT"):
        monkeypatch.delenv(var, raising=False)
        assert unset_env_refs(f"${{{var}}}/hooks") == []


def test_refs_with_a_default_are_not_reported(monkeypatch):
    monkeypatch.delenv("AGENTRY_TEST_TOKEN", raising=False)
    assert unset_env_refs("${AGENTRY_TEST_TOKEN:-fallback}") == []


def test_each_ref_is_reported_once_in_order(monkeypatch):
    for v in ("A_UNSET_ONE", "A_UNSET_TWO"):
        monkeypatch.delenv(v, raising=False)
    text = "${A_UNSET_ONE} ${A_UNSET_TWO} ${A_UNSET_ONE}"
    assert unset_env_refs(text) == ["A_UNSET_ONE", "A_UNSET_TWO"]


def test_text_with_no_refs_is_clean():
    assert unset_env_refs('{"k": "literal"}') == []


# -- Driver ---------------------------------------------------------------


def _driver(**spec_kwargs) -> Driver:
    return Driver(spec=TargetSpec(name="tool", **spec_kwargs))


def test_strategy_returns_none_for_an_unsupported_type():
    """The `| None` return callers branch on — an unannotated signature until now."""
    d = _driver(link={ComponentType.SKILL: ".t/skills/{name}"})
    assert d.strategy(ComponentType.SKILL) is Strategy.LINK
    assert d.strategy(ComponentType.MCP) is None
    assert d.supports(ComponentType.SKILL)
    assert not d.supports(ComponentType.MCP)


def test_link_merge_wins_over_link_for_the_same_type():
    """Precedence matters: a link+merge rule must not be shadowed by a plain link."""
    from agentry.spec import LinkMergeDest, MergeDest

    d = _driver(
        link={ComponentType.HOOK: ".t/hooks/{name}"},
        link_merge={
            ComponentType.HOOK: LinkMergeDest(
                ".t/hooks/{name}", MergeDest(".t/settings.json", "hooks"), "", ""
            )
        },
    )
    assert d.strategy(ComponentType.HOOK) is Strategy.LINK_MERGE


def test_link_dest_expands_the_name_placeholder():
    d = _driver(link={ComponentType.SKILL: ".t/skills/{name}"})
    assert d.link_dest(ComponentType.SKILL, "code-reviewer") == ".t/skills/code-reviewer"


def test_every_builtin_driver_supports_at_least_one_type():
    for name, driver in BUILTIN_DRIVERS.items():
        assert any(driver.supports(t) for t in ComponentType), name


def test_target_profiles_override_a_builtin_driver_per_type():
    """The deep-merge contract: change one type, inherit the rest."""
    config = Config(
        targets=["claude"],
        sources=[],
        components=[],
        repositories=[],
        target_profiles={
            "claude": {
                ComponentType.SKILL: ProfileRule(
                    strategy=Strategy.COPY, dest=".claude/skills/{name}"
                )
            }
        },
        hashing={},
    )
    claude = resolve_drivers(config)["claude"]
    assert claude.strategy(ComponentType.SKILL) is Strategy.COPY
    # Untouched types still come from the built-in driver.
    assert claude.strategy(ComponentType.MCP) is BUILTIN_DRIVERS["claude"].strategy(
        ComponentType.MCP
    )


def test_target_profiles_can_define_a_target_with_no_builtin():
    config = Config(
        targets=["myide"],
        sources=[],
        components=[],
        repositories=[],
        target_profiles={
            "myide": {
                ComponentType.SKILL: ProfileRule(
                    strategy=Strategy.LINK, dest=".myide/skills/{name}"
                )
            }
        },
        hashing={},
    )
    drivers = resolve_drivers(config)
    assert drivers["myide"].link_dest(ComponentType.SKILL, "x") == ".myide/skills/x"


@pytest.mark.parametrize("ctype", list(ComponentType))
def test_filter_hook_events_is_a_noop_without_a_policy(ctype):
    d = _driver(link={ctype: ".t/{name}"})
    entries = {"SomeMadeUpEvent": [{"hooks": []}]}
    kept, dropped = d.filter_hook_events(entries)
    assert kept == entries and dropped == []
