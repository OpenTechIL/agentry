"""Name derivation shared across the source-resolution paths.

Deriving a short, stable name from a git URL or a filesystem path is needed in four
unrelated places (dependency synthesis, ``link+merge`` dest templates, apm import, catalog
source parsing). It lived as four copies of the same four lines; keeping one implementation
means a fix to the locator shapes it handles lands everywhere at once.
"""

from __future__ import annotations

import re

#: Split on either separator. A locator can be a URL (always ``/``) *or* a local filesystem
#: path, and on Windows a local path uses ``\``. Handling only ``/`` meant a Windows local
#: path came back whole — so a `{repo}` template expanded to `C:\Users\...\repo` and the
#: install destination escaped the project. Caught by the Windows CI job.
_SEPARATORS = re.compile(r"[/\\]+")


def repo_basename(locator: str, fallback: str = "") -> str:
    """Last path segment of a git URL or filesystem path, with a trailing ``.git`` removed.

    Handles trailing separators, URL forms (``https://h/o/r.git``), scp-style remotes
    (``git@h:o/r.git``), Windows paths (``C:\\src\\repo``) and mixed separators. The result is
    used as a logical source name and inside destination path templates, so it must never
    contain a separator or a drive letter. Returns ``fallback`` when nothing usable is left.
    """
    tail = _SEPARATORS.split(locator.rstrip("/\\"))[-1]
    # A bare Windows drive-relative path ("C:repo") or an scp-style remote whose path part
    # ended up here ("host:repo") still carries a colon; keep only the final segment.
    if ":" in tail:
        tail = tail.rsplit(":", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    return tail or fallback
