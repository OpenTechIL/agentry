"""Read/write ``.agentry/.manifest.json`` — the record of what is installed.

The manifest is the bridge between *intent* (config + lock) and *reality* (files
on disk). It lets the reconcile engine remove exactly what agentry created and
never touch anything else.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import MANIFEST_NAME, STORE_DIR
from .models import Manifest


def manifest_path(root: Path) -> Path:
    return root / STORE_DIR / MANIFEST_NAME


def load_manifest(root: Path) -> Manifest:
    path = manifest_path(root)
    if not path.is_file():
        return Manifest()
    return Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_manifest(root: Path, manifest: Manifest) -> None:
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
