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


def confined(root: Path, rel: str) -> Path | None:
    """Join ``rel`` onto ``root``, refusing the root itself or anything outside it.

    The last line of defence for path templates that reach the filesystem. Models validate
    them at parse time (:func:`agentry.models._check_rel`), but templates also carry
    ``{name}``-style expansions that are substituted *after* validation, so the join is
    re-checked here. Returns ``None`` when the target escapes ``root``.

    Only the *parent* is resolved, never the final component: the final component is very
    often a symlink agentry itself installed, pointing into the store or at a local source
    outside the project — resolving it would follow that link out and reject our own work.
    The returned path is the unresolved join, because callers depend on seeing the symlink
    (``is_symlink``/``readlink``) rather than its destination.
    """
    root_r = root.resolve()
    target = root / rel
    parent_r = target.parent.resolve()
    if parent_r != root_r and root_r not in parent_r.parents:
        return None
    if parent_r / target.name == root_r:
        return None
    return target


class PathEscapeError(ValueError):
    """A destination template resolved outside the project root."""


def require_confined(root: Path, rel: str) -> Path:
    """Like :func:`confined`, but raise instead of returning ``None``.

    Used at every point where an installer is about to touch the filesystem. An escape
    here means a malformed or hostile path template got past model validation, which is a
    bug or an attack — either way it must be loud, never silently written.
    """
    target = confined(root, rel)
    if target is None:
        raise PathEscapeError(f"refusing to touch {rel!r}: it resolves outside the project root")
    return target
