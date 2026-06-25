"""Download and pin sources into the local store (``.agentry/``).

* **git** sources are cloned once, then checked out (detached) at an exact commit.
* **local** sources are symlinked into the store and content-hashed.

Resolution returns the exact identifier recorded in the lockfile:
a 40-char commit SHA for git, a ``sha256:`` content hash for local.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .config import STORE_DIR
from .models import LockEntry, Source, SourceType


class ResolveError(RuntimeError):
    pass


def store_dir(root: Path) -> Path:
    return root / STORE_DIR


def source_path(root: Path, name: str) -> Path:
    return store_dir(root) / name


def effective_root(root: Path, source: Source) -> Path:
    """Where a source's components actually live — the store clone, plus any ``subdir``.

    Monorepo sources (e.g. a plugin marketplace) keep components under a nested
    directory; ``subdir`` lets discovery and artifact resolution start there.
    """
    base = source_path(root, source.name)
    return base / source.subdir if source.subdir else base


def _git(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ResolveError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def _resolve_git_sha(path: Path, ref: str) -> str:
    """Resolve a ref (branch/tag/commit) to a concrete SHA in a clone."""
    for candidate in (f"origin/{ref}", ref):
        try:
            return _git(["rev-parse", "--verify", f"{candidate}^{{commit}}"], cwd=path)
        except ResolveError:
            continue
    raise ResolveError(f"could not resolve ref '{ref}'")


def _hash_dir(path: Path) -> str:
    """Stable content hash of a directory tree (sorted relpath + bytes)."""
    h = hashlib.sha256()
    root = path.resolve()
    files = sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    for f in files:
        h.update(str(f.relative_to(root)).encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def resolve(root: Path, source: Source, *, pinned: str | None) -> LockEntry:
    """Ensure ``source`` is present in the store and return its lock entry.

    If ``pinned`` is given (from an existing lock), check out exactly that;
    otherwise resolve the source's ref to its current tip.
    """
    store_dir(root).mkdir(parents=True, exist_ok=True)
    dest = source_path(root, source.name)

    if source.type is SourceType.GIT:
        return _resolve_git(source, dest, pinned)
    return _resolve_local(root, source, dest)


def _resolve_git(source: Source, dest: Path, pinned: str | None) -> LockEntry:
    if not (dest / ".git").is_dir():
        if dest.exists() or dest.is_symlink():
            _remove(dest)
        _git(["clone", "--quiet", source.url, str(dest)])
    else:
        _git(["fetch", "--quiet", "--tags", "--force", "origin"], cwd=dest)

    sha = pinned or _resolve_git_sha(dest, source.ref)
    _git(["checkout", "--quiet", "--detach", sha], cwd=dest)
    return LockEntry(
        name=source.name,
        type=SourceType.GIT,
        url=source.url,
        ref=source.ref,
        resolved=sha,
    )


def _resolve_local(root: Path, source: Source, dest: Path) -> LockEntry:
    target = (root / source.path).resolve()
    if not target.is_dir():
        raise ResolveError(f"local source '{source.name}' path not found: {target}")
    # Represent the local source as a symlink in the store so artifact paths resolve.
    if dest.is_symlink() or dest.exists():
        _remove(dest)
    dest.symlink_to(target, target_is_directory=True)
    return LockEntry(
        name=source.name,
        type=SourceType.LOCAL,
        path=source.path,
        resolved=_hash_dir(target),
    )


def _remove(path: Path) -> None:
    import shutil

    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
