"""Copy strategy — copy a file/dir from the store into a target tool's dir.

Unlike :mod:`link`, a copied artifact is self-contained (it does not point back into the
gitignored ``.agentry/`` store), so the target dir can be committed and travels with the
project. The trade-off is that a copy is indistinguishable from a user-authored file on
disk, so *managed-ness* is tracked in the manifest (the caller passes ``managed``), never
inferred from the filesystem — the remover only ever touches paths recorded in the manifest.
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from ._paths import prune_empty_parents


def _files_equal(a: Path, b: Path) -> bool:
    return filecmp.cmp(a, b, shallow=False)


def _dirs_equal(a: Path, b: Path) -> bool:
    """Deep equality of two directory trees (names + file contents)."""
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    match, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    if mismatch or errors:
        return False
    return all(_dirs_equal(a / sub, b / sub) for sub in cmp.common_dirs)


def _same(artifact: Path, dest: Path) -> bool:
    """True if ``dest`` already holds a faithful copy of ``artifact``."""
    if dest.is_symlink():
        return False
    if artifact.is_dir():
        return dest.is_dir() and _dirs_equal(artifact, dest)
    return dest.is_file() and _files_equal(artifact, dest)


def _replace(artifact: Path, dest: Path) -> None:
    """Remove whatever is at ``dest`` and copy ``artifact`` (file or dir) in its place."""
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)
    if artifact.is_dir():
        shutil.copytree(artifact, dest)
    else:
        shutil.copy2(artifact, dest)


def install_copy(root: Path, artifact: Path, dest_rel: str, *, managed: bool) -> str:
    """Copy ``artifact`` to ``dest_rel``. Returns ``"created"``/``"updated"``/``"exists"``.

    ``managed`` says whether agentry already owns the path (per the manifest). An existing
    path that agentry does not own is never overwritten — it raises ``FileExistsError``.
    """
    dest = root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() or dest.is_symlink():
        if not managed:
            raise FileExistsError(f"{dest_rel} already exists and is not managed by agentry")
        if _same(artifact, dest):
            return "exists"
        _replace(artifact, dest)
        return "updated"

    if artifact.is_dir():
        shutil.copytree(artifact, dest)
    else:
        shutil.copy2(artifact, dest)
    return "created"


def remove_copy(root: Path, dest_rel: str) -> bool:
    """Remove a managed copy (file or dir). Called only for manifest-tracked paths."""
    dest = root / dest_rel
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)
    else:
        return False
    prune_empty_parents(root, dest.parent)
    return True


def copy_state(root: Path, artifact: Path, dest_rel: str) -> str:
    """Drift check: ``"ok"``, ``"missing"`` or ``"drift"``."""
    dest = root / dest_rel
    if not (dest.exists() or dest.is_symlink()):
        return "missing"
    return "ok" if _same(artifact, dest) else "drift"
