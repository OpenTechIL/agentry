"""Ensure the local store (``.agentry/``) is git-ignored.

The config (``.agentry.yml``) and lockfile (``.agentry.lock``) stay tracked — only
the downloaded store is ignored, like ``node_modules`` or ``.venv``.
"""

from __future__ import annotations

from pathlib import Path

from .config import STORE_DIR

_ENTRY = f"{STORE_DIR}/"
_HEADER = "# agentry local dependency store (downloaded; do not commit)"


def ensure_gitignore(root: Path) -> bool:
    """Add ``.agentry/`` to ``.gitignore`` if absent. Returns True if changed."""
    path = root / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    stripped = {ln.strip().rstrip("/") for ln in lines}
    if STORE_DIR in stripped:
        return False
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(_HEADER)
    lines.append(_ENTRY)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True
