"""How this CLI was invoked — used in the hints agentry prints back to the user.

agentry installs under three names: ``agentry`` (canonical), plus the short aliases ``agy``
and ``agyx``. ``agy`` is *also* the command for Google's Antigravity CLI, so which name a
user has available on PATH varies. Advice like "run ``agy sync``" is wrong for anyone who
typed ``agentry``, so hint strings interpolate :func:`prog` instead of hardcoding a name.

Deliberately **not** used for text agentry writes to disk (e.g. the ``agy emit`` markers in
a generated AGENTS.md). File content must be byte-stable no matter how the tool was
invoked, so those keep the canonical name.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The name to fall back to when argv[0] is unhelpful (``-c``, a REPL, an embedded call).
CANONICAL = "agentry"

#: Names agentry ships as console scripts. Anything else in argv[0] (a wrapper script, a
#: pytest runner, ``python -m``) means we can't trust it as a command the user can retype.
KNOWN_NAMES = frozenset({CANONICAL, "agy", "agyx"})


def prog() -> str:
    """The command name to put in user-facing hints."""
    try:
        stem = Path(sys.argv[0]).stem
    except (IndexError, TypeError, ValueError):
        return CANONICAL
    return stem if stem in KNOWN_NAMES else CANONICAL
