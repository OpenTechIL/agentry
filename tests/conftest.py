from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentry.config import ConfigStore
from agentry.models import Target


def make_source(root: Path) -> Path:
    """Create a source dir with one of every component type."""
    (root / "skills" / "code-reviewer").mkdir(parents=True)
    (root / "skills" / "code-reviewer" / "SKILL.md").write_text("# code reviewer\n")
    (root / "agents").mkdir()
    (root / "agents" / "planner.md").write_text("# planner\n")
    (root / "commands").mkdir()
    (root / "commands" / "deploy.md").write_text("# deploy\n")
    (root / "tools" / "fmt").mkdir(parents=True)
    (root / "tools" / "fmt" / "run.sh").write_text("echo fmt\n")
    (root / "hooks").mkdir()
    (root / "hooks" / "pre-commit-fmt.json").write_text(
        json.dumps({"pre-commit-fmt": {"command": "fmt", "event": "PreToolUse"}})
    )
    (root / "mcp").mkdir()
    (root / "mcp" / "github.json").write_text(
        json.dumps({"github": {"command": "npx", "args": ["-y", "server-github"]}})
    )
    return root


@pytest.fixture
def local_source(tmp_path: Path) -> Path:
    return make_source(tmp_path / "src")


@pytest.fixture
def nested_source(tmp_path: Path) -> Path:
    """A monorepo-style source: components live under a nested subdir, not the root."""
    repo = tmp_path / "monorepo"
    make_source(repo / "plugins" / "pack")
    return repo


@pytest.fixture
def git_source(tmp_path: Path) -> Path:
    src = make_source(tmp_path / "gitsrc")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.x"}
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=src, check=True, env={**_base_env(), **env})
    return src


def _base_env() -> dict:
    import os

    return dict(os.environ)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, [Target.CLAUDE]).save()
    return proj
