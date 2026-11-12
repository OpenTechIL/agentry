"""Merge strategy — inject a fragment of named entries into a target tool's config file.

Contract: a source fragment (``mcp/<name>.json`` or ``hooks/<name>.json``) is always a
JSON **object of named entries**. Each top-level key is merged under the target's
pointer (e.g. ``mcpServers``); agentry records exactly those keys so they can be
removed later without disturbing hand-added entries.

Example ``mcp/github.json``::

    { "github": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"] } }

**Destination format.** The *source* fragment is always JSON, but the *destination*
config file may be JSON or TOML — chosen by the destination file's extension. A ``.toml``
destination (e.g. Codex's ``.codex/config.toml`` under ``[mcp_servers]``) is read and
written with :mod:`tomlkit`, preserving the user's comments, key order, and unrelated
settings exactly as ``ruamel`` does for ``.agentry.yml``. Everything else is JSON. The
named-entry merge contract and key-scoped reversibility are identical for both.

**Wrapped fragments.** Real-world plugin/MCP files often ship their entries already
*wrapped* under the section name they target — e.g. a Claude Code plugin's
``hooks.json`` is ``{ "description": ..., "hooks": { "Stop": [...] } }`` and an
``.mcp.json`` is ``{ "mcpServers": { ... } }``. :func:`select_entries` unwraps such a
fragment (using the destination's :attr:`~agentry.spec.MergeDest.wrapper_keys`) so
the real named entries — not the wrapper or sibling metadata like ``description`` —
are what gets merged. An already-flat fragment is used as-is.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from pathlib import Path

import tomlkit
import tomlkit.exceptions

from ..spec import MergeDest
from ._paths import require_confined


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


def _is_toml(path: Path) -> bool:
    return path.suffix == ".toml"


class MergeTargetError(ValueError):
    """A merge destination exists but cannot be parsed, so agentry leaves it untouched.

    Raised before anything is written: an unparseable file (truncated JSON, JSONC
    comments, invalid TOML, a non-object root) can't be merged into without losing the
    user's content, so the caller warns and skips that destination instead.
    """


def _read_doc(path: Path, label: str | None = None) -> MutableMapping:
    """Read a config file as a mutable mapping (a tomlkit document for ``.toml``).

    A missing/empty file yields an empty document of the right kind so a later write
    produces the correct format. Both backends return a ``MutableMapping``, so the merge
    logic below is format-agnostic. A file that exists but can't be parsed raises
    :class:`MergeTargetError` naming ``label`` (the project-relative path).
    """
    name = label or str(path)
    if not path.is_file():
        return tomlkit.document() if _is_toml(path) else {}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MergeTargetError(f"{name} is not UTF-8 text — left unchanged") from exc
    if _is_toml(path):
        try:
            return tomlkit.parse(text)
        except tomlkit.exceptions.TOMLKitError as exc:
            raise MergeTargetError(
                f"{name} is not valid TOML ({exc}) — left unchanged; fix it, then re-run sync"
            ) from exc
    if not text.strip():
        return {}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MergeTargetError(
            f"{name} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}) — "
            "left unchanged; fix it (comments and trailing commas are not supported), "
            "then re-run sync"
        ) from exc
    if not isinstance(doc, dict):
        raise MergeTargetError(f"{name} is not a JSON object — left unchanged")
    return doc


def _serialize(path: Path, doc: MutableMapping) -> str:
    """Render ``doc`` in the format chosen by ``path``'s extension.

    JSON is written with ``ensure_ascii=False`` so a user's non-ASCII values (``"héllo"``)
    round-trip verbatim instead of being rewritten as ``\\u`` escapes.
    """
    if _is_toml(path):
        text = tomlkit.dumps(doc)
        return text if text.endswith("\n") else text + "\n"
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _write_doc(path: Path, doc: MutableMapping, *, before: str | None) -> None:
    """Write ``doc`` unless it is unchanged from the ``before`` snapshot.

    ``before`` is the serialization of the document as read (``None`` when the file did not
    exist). Comparing serializations — not raw file bytes — means a merge that changes
    nothing leaves the user's file untouched (bytes, formatting, and mtime), even when its
    formatting differs from ours.
    """
    text = _serialize(path, doc)
    if before is not None and text == before:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def install_merge(root: Path, dest: MergeDest, fragment: dict) -> list[str]:
    """Merge ``fragment`` into the target config; return the owned keys."""
    path = require_confined(root, dest.file)
    doc = _read_doc(path, dest.file)
    before = _serialize(path, doc) if path.is_file() else None
    section = doc.get(dest.pointer)
    if not isinstance(section, MutableMapping):
        doc[dest.pointer] = {}
        # Re-fetch: tomlkit converts the assigned dict into a live Table, so the local
        # must come from the document (a stale plain-dict reference wouldn't persist).
        section = doc[dest.pointer]
    for key, value in fragment.items():
        section[key] = value
    _write_doc(path, doc, before=before)
    return list(fragment.keys())


def remove_merge(root: Path, dest: MergeDest, keys: list[str]) -> bool:
    """Remove agentry-owned ``keys`` from the target config. Leaves the rest intact."""
    path = require_confined(root, dest.file)
    if not path.is_file():
        return False
    doc = _read_doc(path, dest.file)
    section = doc.get(dest.pointer)
    if not isinstance(section, MutableMapping):
        return False
    before = _serialize(path, doc)
    removed = False
    for key in keys:
        if key in section:
            del section[key]
            removed = True
    if not removed:
        return False  # nothing of ours here — never rewrite (or prune) the user's file
    if not section:
        doc.pop(dest.pointer, None)
    _write_doc(path, doc, before=before)
    return removed


def merge_state(root: Path, dest: MergeDest, keys: list[str]) -> str:
    """Drift check: ``"ok"`` if every owned key is present, else ``"missing"``."""
    path = require_confined(root, dest.file)
    if not path.is_file():
        return "missing"
    section = _read_doc(path, dest.file).get(dest.pointer)
    if not isinstance(section, MutableMapping):
        return "missing"
    return "ok" if all(k in section for k in keys) else "missing"
