"""`agentry add <catalog repo>` writes `target_profiles` in a deterministic order.

The synthesized copy/namespaced rules become key order in `.agentry.yml`, so they must not
depend on set iteration (PYTHONHASHSEED): targets follow the project's `targets:` order and
types follow ComponentType order. Each add runs in a fresh interpreter with its own seed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML
from typer.testing import CliRunner

from agentry.cli import app
from agentry.config import ConfigStore
from agentry.models import ComponentType

_TARGETS = ["opencode", "claude", "gemini"]  # deliberately not alphabetical


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "kit"
    for rel in ("commands/c.md", "agents/a.md", "skills/s/SKILL.md"):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")
    return repo


def _add_with_seed(tmp_path: Path, repo: Path, seed: int, monkeypatch) -> bytes:
    catalog = tmp_path / "repositories.json"
    catalog.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": {
                    "kit": {
                        "summary": "k",
                        "source": {"type": "local", "path": str(repo)},
                        "copy": True,
                        "namespaced": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    proj = tmp_path / f"proj-{seed}"
    proj.mkdir()
    ConfigStore.create(proj, list(_TARGETS)).save()
    monkeypatch.chdir(proj)
    assert CliRunner().invoke(app, ["catalog", "add", "r", str(catalog)]).exit_code == 0
    env = {**os.environ, "PYTHONHASHSEED": str(seed)}
    run = subprocess.run(
        [sys.executable, "-c", "from agentry.cli import app; app()", "add", "kit"],
        cwd=proj,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return (proj / ".agentry.yml").read_bytes()


def test_add_writes_target_profiles_in_config_order_for_any_hash_seed(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    outputs = {seed: _add_with_seed(tmp_path, repo, seed, monkeypatch) for seed in (0, 1, 2, 3)}
    assert len(set(outputs.values())) == 1, "`.agentry.yml` differs between hash seeds"

    profiles = YAML(typ="safe").load(outputs[0])["target_profiles"]
    targets = list(profiles)
    assert len(targets) >= 2  # the scenario really synthesizes rules for 2+ targets
    assert targets == [t for t in _TARGETS if t in profiles]  # project `targets:` order
    order = [t.value for t in ComponentType]
    for target, rules in profiles.items():
        types = list(rules)
        assert types == sorted(types, key=order.index), target  # ComponentType order
    assert any(len(rules) >= 2 for rules in profiles.values())  # 2+ types somewhere
