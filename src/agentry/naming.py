"""Name derivation shared across the source-resolution paths.

Deriving a short, stable name from a git URL or path is needed in four unrelated places
(dependency synthesis, link+merge dest templates, apm import, catalog source parsing).
It lived as four copies of the same four lines; keeping one implementation means a fix to
the URL shapes it handles lands everywhere at once.
"""

from __future__ import annotations


def repo_basename(locator: str, fallback: str = "") -> str:
    """Last path segment of a git URL or path, with a trailing ``.git`` removed.

    Handles a trailing slash and works for both URL forms (``https://h/o/r.git``) and
    scp-style remotes (``git@h:o/r.git``), since both end in the repo segment.
    Returns ``fallback`` when the locator yields nothing usable.
    """
    tail = locator.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    return tail or fallback
