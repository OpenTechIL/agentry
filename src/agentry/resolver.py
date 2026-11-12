"""Download and pin sources into the local store (``.agentry/``).

* **git** sources are cloned once, then checked out (detached) at an exact commit.
* **local** sources are symlinked into the store and content-hashed.

Resolution returns the exact identifier recorded in the lockfile:
a 40-char commit SHA for git, a ``sha256:`` content hash for local.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .config import STORE_DIR
from .models import LockEntry, Source, SourceType
from .progname import prog


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


#: Seconds to allow a single git invocation. Clone/fetch reach the network, and stdout is
#: captured — without a cap an unreachable host hangs the CLI silently, with no output.
GIT_TIMEOUT = 300.0


def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        # S603/S607: fixed argv (never a shell string), and `git` is intentionally taken
        # from PATH — pinning an absolute path would break every non-standard install.
        proc = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise ResolveError(f"git {' '.join(args)} timed out after {GIT_TIMEOUT:.0f}s") from exc
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


def _canonical_bytes(data: bytes, normalize: bool) -> bytes:
    """The bytes to hash for one file. With ``normalize``, collapse CRLF/CR→LF for text so the
    hash is OS-independent; binary (non-UTF-8) content is always hashed raw."""
    if not normalize:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data  # binary — leave bytes untouched
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _hash_dir(path: Path, *, normalize: bool = True) -> str:
    """Stable content hash of a directory tree (sorted relpath + bytes).

    When ``normalize`` (the default), text files are hashed in a canonical LF form so the same
    content produces the same hash regardless of the checkout's line endings (see HashingConfig).
    """
    h = hashlib.sha256()
    root = path.resolve()
    files = sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    for f in files:
        h.update(str(f.relative_to(root)).encode("utf-8"))
        h.update(b"\0")
        h.update(_canonical_bytes(f.read_bytes(), normalize))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def resolve(root: Path, source: Source, *, pinned: str | None, normalize: bool = True) -> LockEntry:
    """Ensure ``source`` is present in the store and return its lock entry.

    If ``pinned`` is given (from an existing lock), check out exactly that;
    otherwise resolve the source's ref to its current tip. ``normalize`` controls
    OS-independent line-ending hashing for local sources (see :class:`HashingConfig`).
    """
    store_dir(root).mkdir(parents=True, exist_ok=True)
    dest = source_path(root, source.name)

    if source.type is SourceType.GIT:
        return _resolve_git(source, dest, pinned)
    return _resolve_local(root, source, dest, normalize=normalize)


def _resolve_git(source: Source, dest: Path, pinned: str | None) -> LockEntry:
    if source.url is None:  # normally guaranteed by Source._check_locator
        raise ResolveError(f"git source '{source.name}' has no url")
    if (dest / ".git").is_dir() and _origin_url(dest) != source.url:
        # The configured url moved (a fork, a renamed repo): the cached clone belongs to the
        # old origin, and fetching it would silently install the old repo's content.
        _remove(dest)
    if not (dest / ".git").is_dir():
        if dest.exists() or dest.is_symlink():
            _remove(dest)
        _git(["clone", "--quiet", source.url, str(dest)])
    elif pinned is None or not _has_commit(dest, pinned):
        # Only reach the network when the lock can't be satisfied locally: no pin (update,
        # a changed url/ref, a new source) or the pinned commit isn't in the clone yet.
        # With the pin present, sync/status/doctor/--frozen stay fully offline.
        _git(["fetch", "--quiet", "--tags", "--force", "origin"], cwd=dest)
    # After a fresh clone or a fetch, a pin that's still missing was force-pushed away.
    if pinned is not None and not _has_commit(dest, pinned):
        raise ResolveError(
            f"source '{source.name}': locked commit {pinned[:12]} is no longer on origin "
            f"(force-pushed or deleted?). Run `{prog()} update {source.name}` to re-pin."
        )

    sha = pinned or _resolve_git_sha(dest, source.ref)
    _git(["checkout", "--quiet", "--detach", sha], cwd=dest)
    return LockEntry(
        name=source.name,
        type=SourceType.GIT,
        url=source.url,
        ref=source.ref,
        resolved=sha,
    )


def _has_commit(clone: Path, sha: str) -> bool:
    """Whether ``sha`` is a commit already present in the clone (local, never raises).

    Any git error counts as "not present", so the caller falls back to fetching.
    """
    try:
        _git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd=clone)
    except (ResolveError, OSError):
        return False
    return True


def _origin_url(clone: Path) -> str | None:
    """The ``origin`` remote url of a store clone, or ``None`` if it has none/is broken."""
    try:
        return _git(["remote", "get-url", "origin"], cwd=clone)
    except ResolveError:
        return None


def _resolve_local(root: Path, source: Source, dest: Path, *, normalize: bool = True) -> LockEntry:
    if source.path is None:  # normally guaranteed by Source._check_locator
        raise ResolveError(f"local source '{source.name}' has no path")
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
        resolved=_hash_dir(target, normalize=normalize),
    )


def _force_writable(func: Callable[..., object], path: str, _exc: object) -> None:
    """rmtree error hook: clear the read-only bit and retry.

    git marks pack files (``.git/objects/pack/*.idx`` / ``*.pack``) read-only. POSIX lets
    you unlink them anyway (removal is a directory permission), but Windows refuses with
    ``PermissionError [WinError 5]`` — so removing a store clone crashed on Windows.
    """
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    parent = os.path.dirname(path)
    if parent:  # a read-only directory also blocks unlinking its entries (POSIX)
        os.chmod(parent, stat.S_IRWXU)
    func(path)


def _remove(path: Path) -> None:
    if path.is_symlink():
        path.unlink()  # the link itself — never touch (or chmod) what it points at
    elif path.is_file():
        # A read-only file can't be unlinked on Windows (PermissionError [WinError 5]).
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()
    elif path.is_dir():
        # `onexc` replaced `onerror` in Python 3.12 (and `onerror` is deprecated there);
        # both receive (func, path, exc-or-excinfo), which the hook ignores.
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_force_writable)
        else:
            shutil.rmtree(path, onerror=_force_writable)
