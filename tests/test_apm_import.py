"""Tests for the apm.yml → agentry translator (apm_import)."""

from __future__ import annotations

from agentry.apm_import import parse_apm_dep, translate_apm
from agentry.models import ComponentType, SourceType

# -- dependency-spec parsing (pure) --------------------------------------


def test_parse_github_shorthand_with_typedir_and_name():
    dep = parse_apm_dep("github/awesome-copilot/skills/review-and-refactor")
    assert dep.kind == "git"
    assert dep.url == "https://github.com/github/awesome-copilot"
    assert dep.repo == "awesome-copilot"
    assert dep.typedir == "skills" and dep.component == "review-and-refactor"
    assert dep.ref is None


def test_parse_anthropics_skills_nested():
    dep = parse_apm_dep("anthropics/skills/skills/skill-creator")
    assert dep.url == "https://github.com/anthropics/skills"
    assert dep.repo == "skills"
    assert dep.typedir == "skills" and dep.component == "skill-creator"


def test_parse_ref_pin():
    dep = parse_apm_dep("microsoft/apm-sample-package#v1.0.0")
    assert dep.kind == "git" and dep.repo == "apm-sample-package" and dep.ref == "v1.0.0"
    assert dep.typedir is None and dep.component is None


def test_parse_fqdn_host_shorthand():
    dep = parse_apm_dep("gitlab.com/acme/repo/skills/x")
    assert dep.url == "https://gitlab.com/acme/repo"
    assert dep.typedir == "skills" and dep.component == "x"


def test_parse_full_git_url():
    dep = parse_apm_dep("https://gitlab.com/acme/coding-standards.git")
    assert dep.kind == "url" and dep.repo == "coding-standards"
    assert dep.url == "https://gitlab.com/acme/coding-standards.git"


def test_parse_local_path():
    dep = parse_apm_dep("./packages/apm-issue-autopilot")
    assert dep.kind == "local" and dep.path == "./packages/apm-issue-autopilot"
    assert dep.repo == "apm-issue-autopilot"


def test_parse_marketplace_is_flagged():
    assert parse_apm_dep("code-review@acme-plugins#v2.0.0").kind == "marketplace"


def test_parse_bundle_is_flagged():
    assert parse_apm_dep("./bundle.tar.gz").kind == "bundle"


# -- whole-document translation ------------------------------------------


def test_translate_full_manifest():
    doc = {
        "name": "demo",
        "targets": ["copilot", "claude"],
        "dependencies": {
            "apm": [
                "github/awesome-copilot/skills/review-and-refactor",  # -> source + component
                "./packages/local-pkg",  # -> local source (no component, warns)
                "openshift-eng/ai-helpers/plugins/bigquery",  # -> source only (plugins, warns)
                "code-review@acme-plugins#v2.0.0",  # -> skipped
            ],
            "mcp": [
                {
                    "name": "github",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PAT}"},
                },
                {
                    "name": "atlassian",
                    "transport": "http",
                    "url": "https://mcp.atlassian.com/v1/mcp",
                },
            ],
        },
    }
    res = translate_apm(doc)

    assert res.targets == ["copilot", "claude"]
    by_name = {s.name: s for s in res.sources}
    assert by_name["awesome-copilot"].type is SourceType.GIT
    assert by_name["awesome-copilot"].url == "https://github.com/github/awesome-copilot"
    assert by_name["local-pkg"].type is SourceType.LOCAL
    assert "ai-helpers" in by_name  # plugins dep still adds the source

    # Exactly one component could be inferred (the skills/<name> dep).
    assert [(c.source, c.type, c.name) for c in res.components] == [
        ("awesome-copilot", ComponentType.SKILL, "review-and-refactor")
    ]

    # MCP servers become fragments keyed by name, in agentry's merge shape.
    assert res.mcp_fragments["github"] == {
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PAT}"},
        }
    }
    assert res.mcp_fragments["atlassian"] == {
        "atlassian": {"url": "https://mcp.atlassian.com/v1/mcp"}
    }

    # The marketplace, plugins, and local deps each produced a warning.
    joined = "\n".join(res.warnings)
    assert "marketplace" in joined
    assert "ai-helpers" in joined  # couldn't infer a component
    assert "local-pkg" in joined


def test_translate_empty_manifest_is_safe():
    res = translate_apm({"name": "x", "version": "1.0.0"})
    assert res.sources == [] and res.components == [] and res.mcp_fragments == {}


def test_translate_includes_auto_warns():
    res = translate_apm({"includes": "auto", "dependencies": {"apm": [], "mcp": []}})
    assert any("includes: auto" in w for w in res.warnings)
