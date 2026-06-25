"""Link strategy — symlink a file/dir from the store into a target tool's dir.

Symlinks are created **relative** so the project stays portable. The remover only
ever touches symlinks that resolve into the store, so user-owned files and
unrelated links are never deleted.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..resolver import store_dir
from ._paths import prune_empty_parents


def _link_target(artifact: Path, dest: Path) -> str:
    """Relative symlink target, computed *lexically* so it routes through the store.

    We use ``abspath`` (not ``resolve``) on purpose: local sources are represented
    as a symlink inside ``.agentry/``, and resolving would collapse that out of the
    path — making our own links look unmanaged.
    """
    return os.path.relpath(os.path.abspath(artifact), os.path.abspath(dest.parent))


def is_managed_link(root: Path, path: Path) -> bool:
    """True if ``path`` is a symlink whose (lexical) target lives in agentry's store."""
    if not path.is_symlink():
        return False
    try:
        target = os.path.abspath(os.path.join(os.path.dirname(path), os.readlink(path)))
    except OSError:
        return False
    store = os.path.abspath(store_dir(root))
    return target == store or target.startswith(store + os.sep)


def install_link(root: Path, artifact: Path, dest_rel: str) -> str:
    """Create/refresh a symlink at ``dest_rel`` pointing at ``artifact``.

    Returns one of ``"created"``, ``"updated"``, ``"exists"``.
    Refuses to overwrite a path that isn't already a managed link.
    """
    dest = root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    rel_target = _link_target(artifact, dest)

    if dest.is_symlink():
        if not is_managed_link(root, dest):
            raise FileExistsError(f"{dest_rel} is a symlink agentry does not manage")
        if os.readlink(dest) == rel_target:
            return "exists"
        dest.unlink()
        dest.symlink_to(rel_target)
        return "updated"
    if dest.exists():
        raise FileExistsError(f"{dest_rel} already exists and is not managed by agentry")

    dest.symlink_to(rel_target)
    return "created"


def remove_link(root: Path, dest_rel: str) -> bool:
    """Remove a managed symlink. No-op (returns False) if it isn't ours."""
    dest = root / dest_rel
    if is_managed_link(root, dest):
        dest.unlink()
        prune_empty_parents(root, dest.parent)
        return True
    return False


def link_state(root: Path, artifact: Path, dest_rel: str) -> str:
    """Drift check: ``"ok"``, ``"missing"`` or ``"drift"``."""
    dest = root / dest_rel
    if not dest.is_symlink():
        return "missing"
    if not is_managed_link(root, dest):
        return "drift"
    return "ok" if os.readlink(dest) == _link_target(artifact, dest) else "drift"
