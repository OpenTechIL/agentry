"""Generate strategy — install a component by running *its own* CLI.

Some tools (e.g. graphify) ship no symlinkable artifact: they generate their skill files
at install time via their own command. agentry runs that command and records the paths it
declares it ``produces`` so removal can delete exactly those — nothing else.

Running third-party commands is gated: the reconcile engine only calls :func:`run_generator`
when the user passes ``--allow-run``. Removal (:func:`remove_generated`) never runs code, so
it is always allowed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..models import GeneratorSpec


class GenerateError(RuntimeError):
    pass


def produces_present(root: Path, spec: GeneratorSpec) -> bool:
    """True when every declared output already exists (used for idempotency)."""
    return all((root / p).exists() for p in spec.produces)


def describe(spec: GeneratorSpec) -> list[str]:
    """Human-readable argv lines, for the confirmation prompt before running."""
    return [" ".join(cmd) for cmd in (*spec.setup, spec.command)]


def run_generator(root: Path, spec: GeneratorSpec) -> None:
    """Run ``setup`` commands then ``command`` from the project root (no shell)."""
    for cmd in (*spec.setup, spec.command):
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise GenerateError(f"`{' '.join(cmd)}` failed (exit {proc.returncode}):\n{detail}")


def _confined(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``root``, refusing the root itself or anything outside it."""
    root = root.resolve()
    target = (root / rel).resolve()
    if target == root or root not in target.parents:
        return None
    return target


def remove_generated(root: Path, paths: list[str]) -> list[str]:
    """Delete the recorded produced paths (files or dirs). Returns the ones actually removed."""
    removed: list[str] = []
    for rel in paths:
        target = _confined(root, rel)
        if target is None or not (target.exists() or target.is_symlink()):
            continue
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(rel)
    return removed
