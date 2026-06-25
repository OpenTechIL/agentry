"""Shared filesystem helpers for installers (link, copy)."""

from __future__ import annotations

from pathlib import Path


def prune_empty_parents(root: Path, directory: Path) -> None:
    """Remove now-empty managed parent dirs (e.g. .claude/skills) up to ``root``."""
    root = root.resolve()
    cur = directory.resolve()
    while cur != root and cur.is_dir() and not any(cur.iterdir()):
        cur.rmdir()
        cur = cur.parent
