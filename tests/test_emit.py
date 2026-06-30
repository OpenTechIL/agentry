"""Tests for AGENTS.md emit (emit.py) — the deterministic half of the transform seam."""

from __future__ import annotations

from agentry.emit import EmitItem, compose_agents_md
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
