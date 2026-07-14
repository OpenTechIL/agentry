"""Tests for AGENTS.md emit (emit.py) — the deterministic half of the transform seam."""

from __future__ import annotations

import pytest

from agentry.emit import (
    TRIGGERS_BEGIN,
    TRIGGERS_END,
    EmitItem,
    TransformError,
    _frontmatter_description,
    build_synthesis_prompt,
    compose_agents_md,
    compose_triggers_block,
    merge_managed_block,
    run_agent,
)
from agentry.models import ComponentType

_C = ComponentType


def _skill(name: str, description: str | None) -> EmitItem:
    fm = f"---\nname: {name}\n"
    if description is not None:
        fm += f"description: {description}\n"
    fm += "---\n# body\nignored prose\n"
    return EmitItem(_C.SKILL, name, fm)


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
    with pytest.raises(TransformError):
        run_agent([], "x")  # no command configured
    with pytest.raises(TransformError):
        run_agent(["this-command-does-not-exist-xyz"], "x")  # not found


# -- skill-trigger registration ----------------------------------------------


def test_frontmatter_description_extracted_and_collapsed():
    text = "---\nname: x\ndescription: Use when the user\n  wants a greeting.\n---\nbody\n"
    assert _frontmatter_description(text) == "Use when the user wants a greeting."


def test_frontmatter_description_missing_or_no_frontmatter():
    assert _frontmatter_description("no frontmatter here\n") is None
    assert _frontmatter_description("---\nname: x\n---\nbody\n") is None


def test_compose_triggers_block_maps_name_to_description():
    block = compose_triggers_block(
        [
            _skill("code-reviewer", "Use when reviewing a diff before merging."),
            _skill("pdf-processing", "Use when extracting text from PDFs."),
        ]
    )
    assert block.startswith(TRIGGERS_BEGIN)
    assert block.endswith(TRIGGERS_END)
    assert "- **code-reviewer** — Use when reviewing a diff before merging." in block
    assert "- **pdf-processing** — Use when extracting text from PDFs." in block
    # The SKILL.md body must not leak into the trigger block.
    assert "ignored prose" not in block


def test_compose_triggers_block_missing_description_degrades():
    block = compose_triggers_block([_skill("bare", None)])
    assert "- **bare**" in block
    assert "- **bare** —" not in block


def test_merge_appends_block_when_absent_preserving_content():
    existing = "# CLAUDE.md\n\nHand-written guidance.\n"
    block = compose_triggers_block([_skill("greeter", "Use when greeting.")])
    merged = merge_managed_block(existing, block)
    assert merged.startswith("# CLAUDE.md\n\nHand-written guidance.")
    assert TRIGGERS_BEGIN in merged and TRIGGERS_END in merged


def test_merge_replaces_only_the_managed_span():
    block1 = compose_triggers_block([_skill("greeter", "Use when greeting.")])
    block2 = compose_triggers_block([_skill("greeter", "Use when saying hi.")])
    existing = "TOP\n\n" + block1 + "\n\nBOTTOM\n"
    merged = merge_managed_block(existing, block2)
    assert merged.startswith("TOP\n")
    assert merged.rstrip().endswith("BOTTOM")
    assert "Use when saying hi." in merged
    assert "Use when greeting." not in merged
    assert merged.count(TRIGGERS_BEGIN) == 1


def test_merge_is_idempotent():
    block = compose_triggers_block([_skill("greeter", "Use when greeting.")])
    once = merge_managed_block("# CLAUDE.md\n\nprose\n", block)
    assert merge_managed_block(once, block) == once


def test_merge_into_empty_string():
    block = compose_triggers_block([_skill("greeter", "Use when greeting.")])
    assert merge_managed_block("", block) == block + "\n"


def test_merge_raises_on_unbalanced_begin():
    block = compose_triggers_block([_skill("greeter", "Use when greeting.")])
    with pytest.raises(ValueError, match="without a matching"):
        merge_managed_block(f"prose\n{TRIGGERS_BEGIN}\norphan\n", block)
