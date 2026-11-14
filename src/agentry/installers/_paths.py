"""Shared filesystem helpers for installers (link, copy)."""

from __future__ import annotations

from pathlib import Path


def _rel(root: Path, path: Path) -> str | None:
    """``path`` relative to ``root`` (posix), or ``None`` if it is root or outside it."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    return rel.as_posix() if rel.parts else None


def missing_dirs(root: Path, directory: Path) -> list[str]:
    """The folders between ``root`` and ``directory`` that don't exist yet (project-relative,
    deepest first) — what a ``mkdir(parents=True)`` there is about to create."""
    out: list[str] = []
    d = directory
    while not d.exists():
        rel = _rel(root, d)
        if rel is None:
            break
        out.append(rel)
        d = d.parent
    return out


def record_created(root: Path, directory: Path, created: set[str] | None) -> None:
    """``mkdir -p directory``, adding the folders it creates to ``created`` (if given)."""
    if created is not None:
        created.update(missing_dirs(root, directory))
    directory.mkdir(parents=True, exist_ok=True)


def prune_created_dirs(root: Path, directory: Path, created: set[str] | None) -> None:
    """Remove folders agentry recorded creating, now completely empty, deepest first.

    Stops at the first folder that isn't recorded, isn't empty (hidden files count), is a
    symlink, is agentry's ``.agentry/`` store, or is the project root / outside it. A folder
    the user made — or one an older agentry made without recording it — is never removed.
    ``created`` is ``None`` ⇒ nothing is pruned.
    """
    if created is None:
        return
    d = directory
    while True:
        rel = _rel(root, d)
        if rel is None or rel not in created or rel.split("/", 1)[0] == ".agentry":
            return
        if d.is_symlink() or not d.is_dir() or any(d.iterdir()):
            return
        d.rmdir()
        created.discard(rel)
        d = d.parent


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
