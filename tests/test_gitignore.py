from __future__ import annotations

from pathlib import Path

from agentry.gitignore import ensure_gitignore


def test_adds_entry_once(tmp_path: Path):
    assert ensure_gitignore(tmp_path) is True
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".agentry/" in content
    # idempotent
    assert ensure_gitignore(tmp_path) is False
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == content


def test_preserves_existing(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    ensure_gitignore(tmp_path)
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in content
    assert ".agentry/" in content
