"""Merge strategy — inject a JSON fragment into a target tool's config file.

Contract: a source fragment (``mcp/<name>.json`` or ``hooks/<name>.json``) is a
JSON **object of named entries**. Each top-level key is merged under the target's
pointer (e.g. ``mcpServers``); agentry records exactly those keys so they can be
removed later without disturbing hand-added entries.

Example ``mcp/github.json``::

    { "github": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"] } }

**Wrapped fragments.** Real-world plugin/MCP files often ship their entries already
*wrapped* under the section name they target — e.g. a Claude Code plugin's
``hooks.json`` is ``{ "description": ..., "hooks": { "Stop": [...] } }`` and an
``.mcp.json`` is ``{ "mcpServers": { ... } }``. :func:`select_entries` unwraps such a
fragment (using the destination's :attr:`~agentry.targets.MergeDest.wrapper_keys`) so
the real named entries — not the wrapper or sibling metadata like ``description`` —
are what gets merged. An already-flat fragment is used as-is.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..targets import MergeDest


def load_fragment(artifact: Path) -> dict:
    data = json.loads(artifact.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{artifact} must be a JSON object of named entries")
    return data


def select_entries(fragment: dict, dest: MergeDest) -> dict:
    """Return the named entries to merge, unwrapping a canonical wrapper if present.

    A fragment may ship its entries wrapped under the section name it targets
    (e.g. ``{"hooks": {...}}`` or ``{"mcpServers": {...}}``), optionally alongside
    metadata like a top-level ``description``. When a wrapper key (see
    :attr:`MergeDest.wrapper_keys`) is present with an object value, those inner
    entries are the real payload and any sibling keys are ignored. An already-flat
    fragment is returned unchanged.
    """
    for key in dest.wrapper_keys:
        inner = fragment.get(key)
        if isinstance(inner, dict):
            return inner
    return fragment


def _read_doc(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a JSON object")
    return doc


def _write_doc(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def install_merge(root: Path, dest: MergeDest, fragment: dict) -> list[str]:
    """Merge ``fragment`` into the target config; return the owned keys."""
    path = root / dest.file
    doc = _read_doc(path)
    section = doc.get(dest.pointer)
    if not isinstance(section, dict):
        section = {}
        doc[dest.pointer] = section
    for key, value in fragment.items():
        section[key] = value
    _write_doc(path, doc)
    return list(fragment.keys())


def remove_merge(root: Path, dest: MergeDest, keys: list[str]) -> bool:
    """Remove agentry-owned ``keys`` from the target config. Leaves the rest intact."""
    path = root / dest.file
    if not path.is_file():
        return False
    doc = _read_doc(path)
    section = doc.get(dest.pointer)
    if not isinstance(section, dict):
        return False
    removed = False
    for key in keys:
        if key in section:
            del section[key]
            removed = True
    if not section:
        doc.pop(dest.pointer, None)
    _write_doc(path, doc)
    return removed


def merge_state(root: Path, dest: MergeDest, keys: list[str]) -> str:
    """Drift check: ``"ok"`` if every owned key is present, else ``"missing"``."""
    path = root / dest.file
    if not path.is_file():
        return "missing"
    section = _read_doc(path).get(dest.pointer)
    if not isinstance(section, dict):
        return "missing"
    return "ok" if all(k in section for k in keys) else "missing"
