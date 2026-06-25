"""Read/write ``.agentry.lock`` — the resolved, pinned state of every source."""

from __future__ import annotations

import json
from pathlib import Path

from .config import LOCK_NAME
from .models import Lock, LockEntry


def lock_path(root: Path) -> Path:
    return root / LOCK_NAME


def load_lock(root: Path) -> Lock:
    path = lock_path(root)
    if not path.is_file():
        return Lock()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Lock.model_validate(data)


def save_lock(root: Path, lock: Lock) -> None:
    path = lock_path(root)
    payload = lock.model_dump(mode="json", exclude_none=True)
    # Keep the on-disk shape minimal: only mark transitive (synthesized) sources.
    for entry in payload.get("sources", []):
        if entry.get("synthesized") is False:
            entry.pop("synthesized", None)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def upsert_entry(lock: Lock, entry: LockEntry) -> None:
    """Replace or append a source's lock entry in place."""
    for i, e in enumerate(lock.sources):
        if e.name == entry.name:
            lock.sources[i] = entry
            return
    lock.sources.append(entry)


def prune(lock: Lock, keep_names: set[str]) -> None:
    """Drop lock entries for sources no longer in the config."""
    lock.sources[:] = [e for e in lock.sources if e.name in keep_names]
