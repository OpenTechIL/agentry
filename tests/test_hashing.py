"""Deterministic, OS-independent content hashing for local sources (apm pain-points idea 1).

By default agentry hashes text in a canonical LF form, so the same content yields the same
``sha256:`` regardless of a checkout's line endings (the CRLF trap behind apm #1952/#1889).
The behavior is configurable: ``hashing.normalize_line_endings: false`` restores raw bytes.
"""

from __future__ import annotations

from pathlib import Path

from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync
from agentry.resolver import _hash_dir


def _write(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_line_endings_do_not_change_hash_by_default(tmp_path: Path):
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    _write(lf / "skills" / "s" / "SKILL.md", b"# title\nline two\n")
    _write(crlf / "skills" / "s" / "SKILL.md", b"# title\r\nline two\r\n")
    assert _hash_dir(lf) == _hash_dir(crlf)


def test_raw_hashing_distinguishes_line_endings_when_disabled(tmp_path: Path):
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    _write(lf / "f.md", b"a\nb\n")
    _write(crlf / "f.md", b"a\r\nb\r\n")
    assert _hash_dir(lf, normalize=False) != _hash_dir(crlf, normalize=False)


def test_binary_content_change_still_detected(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a / "img.bin", b"\x00\x01\x02\xff")
    _write(b / "img.bin", b"\x00\x01\x02\xfe")  # one byte differs
    assert _hash_dir(a) != _hash_dir(b)
    # And identical binary content hashes identically (normalization never touches it).
    c = tmp_path / "c"
    _write(c / "img.bin", b"\x00\x01\x02\xff")
    assert _hash_dir(a) == _hash_dir(c)


def test_sync_reports_no_drift_after_line_ending_change(project: Path, local_source: Path):
    store = ConfigStore.load(project)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=ComponentType.SKILL, name="code-reviewer"))
    store.save()
    sync(project)
    locked = (project / ".agentry.lock").read_text()

    # Rewrite a source file LF→CRLF (as a Windows checkout would): no real content change.
    skill = local_source / "skills" / "code-reviewer" / "SKILL.md"
    skill.write_bytes(skill.read_text().replace("\n", "\r\n").encode("utf-8"))

    rows, _ = status(project)
    assert all(r.state == "ok" for r in rows)
    # Re-resolving doesn't churn the lock hash either.
    sync(project)
    assert (project / ".agentry.lock").read_text() == locked


def test_hashing_config_round_trips(project: Path):
    store = ConfigStore.load(project)
    assert store.parsed().hashing.normalize_line_endings is True  # default
    store.doc["hashing"] = {"normalize_line_endings": False}
    store.save()
    assert ConfigStore.load(project).parsed().hashing.normalize_line_endings is False
