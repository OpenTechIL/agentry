"""Tests for AGENTS.md emit (emit.py) — the deterministic half of the transform seam."""

from __future__ import annotations

from agentry.emit import (
    EmitItem,
    TransformError,
    build_synthesis_prompt,
    compose_agents_md,
    run_agent,
)
from agentry.models import ComponentType

_C = ComponentType


def test_compose_strips_frontmatter_and_sections_by_component():
    items = [
        EmitItem(_C.SKILL, "code-reviewer", "---\nname: code-reviewer\n---\nReview carefully.\n"),
        EmitItem(_C.AGENT, "planner", "Plan first.\n"),
    ]
    out = compose_agents_md(items)
    assert out.startswith("# AGENTS.md\n")
    assert "## code-reviewer (skill)" in out
    assert "## planner (agent)" in out
    assert "Review carefully." in out and "Plan first." in out
    # Frontmatter is dropped from the composed body.
    assert "name: code-reviewer" not in out


def test_compose_is_deterministic():
    items = [EmitItem(_C.SKILL, "a", "Body A\n"), EmitItem(_C.COMMAND, "b", "Body B\n")]
    assert compose_agents_md(items) == compose_agents_md(items)


def test_compose_empty_is_just_header():
    out = compose_agents_md([])
    assert out.startswith("# AGENTS.md")
    assert out.endswith("\n")


def test_build_synthesis_prompt_includes_components():
    items = [EmitItem(_C.SKILL, "code-reviewer", "Review carefully.")]
    prompt = build_synthesis_prompt(items)
    assert "AGENTS.md" in prompt
    assert "code-reviewer (skill)" in prompt and "Review carefully." in prompt


def test_run_agent_pipes_prompt_through_command():
    # `cat` echoes stdin → stdout, standing in for an agent CLI.
    assert run_agent(["cat"], "hello world") == "hello world\n"


def test_run_agent_errors_are_typed():
    import pytest

    with pytest.raises(TransformError):
        run_agent([], "x")  # no command configured
    with pytest.raises(TransformError):
        run_agent(["this-command-does-not-exist-xyz"], "x")  # not found
