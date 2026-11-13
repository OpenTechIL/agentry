"""Component identity beyond the install path (ADR-003).

Claude Code identifies an agent by the ``name`` in its frontmatter, not by where the file
lives: it scans ``.claude/agents/`` recursively, and when two files declare one name it loads
only one of them. A target declares this as data (``TargetSpec.identity``), and reconcile
turns it into *name units* in the claim-order pass.

Everything here only reads files. Reading never raises: a malformed, binary or unreadable
file yields no name.
"""

from __future__ import annotations

import os
from pathlib import Path

from ruamel.yaml import YAML

from .emit import _frontmatter_block

#: The one identity rule there is today: the artifact's frontmatter ``name``.
FRONTMATTER_NAME = "frontmatter:name"

#: Read at most this much of a file for its frontmatter.
_MAX_READ = 64 * 1024


def frontmatter_name(path: Path) -> str | None:
    """The ``name`` in ``path``'s leading YAML frontmatter, or ``None`` (never raises)."""
    try:
        with path.open("rb") as fh:
            raw = fh.read(_MAX_READ)
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    block = _frontmatter_block(text)
    if block is None:
        return None
    try:
        data = YAML(typ="safe").load(block)
    except Exception:  # noqa: BLE001 — malformed frontmatter is "no name", never a crash
        return None
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def scan_dir(template: str) -> str | None:
    """The directory a dest template installs into, up to its first ``{placeholder}``.

    ``.claude/agents/{name}.md`` → ``.claude/agents``. ``None`` for a template that is
    absolute or climbs out of the project (the scan stays inside it).
    """
    head = template.split("{", 1)[0]
    parent = head if head.endswith("/") else head.rsplit("/", 1)[0] if "/" in head else ""
    parent = parent.strip("/")
    if not parent or os.path.isabs(template) or ".." in Path(parent).parts:
        return None
    return parent


def scan_names(root: Path, dirs: list[str], skip: set[str]) -> dict[str, str]:
    """``casefold(name) -> project-relative path`` for unmanaged agents under ``dirs``.

    Recursive, inside the project only; symlinks (files and dirs) are never followed or
    read, and paths in ``skip`` (agentry's own installs, casefolded) are ignored. The first
    file found for a name is kept. Files without a readable ``name`` are skipped silently.
    """
    found: dict[str, str] = {}
    roots = sorted(set(dirs))
    # Drop a root nested inside another: the walk already covers it.
    roots = [r for r in roots if not any(r != o and r.startswith(o + "/") for o in roots)]
    for rel_root in roots:
        top = root / rel_root
        if top.is_symlink() or not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            dirnames.sort()
            for fname in sorted(filenames):
                if not fname.endswith(".md"):
                    continue
                path = Path(dirpath) / fname
                rel = path.relative_to(root).as_posix()
                if path.is_symlink() or rel.casefold() in skip:
                    continue
                name = frontmatter_name(path)
                if name is not None:
                    found.setdefault(name.casefold(), rel)
    return found
