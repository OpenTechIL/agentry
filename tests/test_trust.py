"""Per-source consent for install-time code execution (apm pain-points idea 4).

A component with a ``generate`` spec runs code during ``agy sync``. Beyond the one-shot
``--allow-run`` blanket, agentry records **per-source trust** in the lock, pinned to the
source's resolved SHA: a trusted source runs without ``--allow-run``, and trust is dropped
the moment the source's content changes (so a moved/edited source must be re-confirmed).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from agentry.config import ConfigStore
from agentry.lockfile import load_lock
from agentry.models import Component, ComponentType, GeneratorSpec, Source, SourceType
from agentry.reconcile import sync


def _generator() -> GeneratorSpec:
    produced = ".claude/skills/fake/SKILL.md"
    script = (
        "import os;"
        "os.makedirs(os.path.join(os.getcwd(), '.claude/skills/fake'), exist_ok=True);"
        f"open(os.path.join(os.getcwd(), {produced!r}), 'w').write('# fake\\n')"
    )
    return GeneratorSpec(command=[sys.executable, "-c", script], produces=[".claude/skills/fake"])


def _wire(project: Path, source: Path) -> None:
    store = ConfigStore.load(project)
    store.add_source(Source(name="g", type=SourceType.LOCAL, path=str(source)))
    store.add_component(
        Component(source="g", type=ComponentType.SKILL, name="fake", generate=_generator())
    )
    store.save()


def test_untrusted_generator_is_skipped(project: Path, local_source: Path):
    _wire(project, local_source)
    res = sync(project)  # no allow_run, no trust, no callback
    assert not (project / ".claude/skills/fake").exists()
    assert any("not trusted" in w and "agy trust g" in w for w in res.warnings)


def test_trusted_source_runs_without_allow_run(project: Path, local_source: Path):
    _wire(project, local_source)
    sync(project)  # resolves the source into the lock
    # Record consent (what `agy trust g` does).
    from agentry.lockfile import save_lock

    lock = load_lock(project)
    lock.entry("g").trusted = True
    save_lock(project, lock)

    res = sync(project)  # still no allow_run
    assert (project / ".claude/skills/fake/SKILL.md").read_text() == "# fake\n"
    assert any("generated g/skill/fake" in c for c in res.created)


def test_trust_callback_grants_and_persists(project: Path, local_source: Path):
    _wire(project, local_source)
    granted = []

    def cb(source: str, sha: str) -> bool:
        granted.append((source, sha))
        return True

    res = sync(project, trust_callback=cb)
    assert granted and granted[0][0] == "g"
    assert (project / ".claude/skills/fake/SKILL.md").exists()
    # Consent persisted to the lock, pinned to the resolved SHA.
    assert load_lock(project).entry("g").trusted is True
    assert any("generated g/skill/fake" in c for c in res.created)
    # And it's durable: a later sync needs neither callback nor --allow-run.
    res2 = sync(project)
    assert not any("not trusted" in w for w in res2.warnings)


def test_trust_dropped_when_source_sha_changes(project: Path, local_source: Path):
    _wire(project, local_source)
    sync(project, trust_callback=lambda s, sha: True)
    assert load_lock(project).entry("g").trusted is True

    # The source content changes → new hash → consent no longer applies.
    (local_source / "NEWFILE.md").write_text("changed\n")
    shutil.rmtree(project / ".claude/skills/fake")  # force a re-run attempt
    res = sync(project)  # no callback this time
    assert load_lock(project).entry("g").trusted is False
    assert any("not trusted" in w for w in res.warnings)


def test_allow_run_still_bypasses_trust(project: Path, local_source: Path):
    _wire(project, local_source)
    res = sync(project, allow_run=True)  # blanket override, no trust recorded
    assert (project / ".claude/skills/fake/SKILL.md").exists()
    assert load_lock(project).entry("g").trusted is False  # not persisted by --allow-run
    assert any("generated g/skill/fake" in c for c in res.created)
