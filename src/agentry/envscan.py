"""Detect dead ``${VAR}`` placeholders in MCP/hook fragments.

agentry's deliberate contract (see ``docs/architecture.md`` §7): a merge fragment's
``${VAR}`` references are shipped **verbatim** — the runtime agent resolves them, not
agentry. The one failure that contract can't catch by itself is a reference that is
*unset and has no default*: it ships a placeholder that resolves to nothing. Both
``agy doctor`` and ``agy sync`` scan for exactly that case and warn loudly rather than
silently installing a dead placeholder. A reference *with* a default
(``${VAR:-x}`` / ``${VAR:=x}`` / ``${VAR:x}``) can never be dead, so it is not flagged.

One family is *never* flagged regardless: ``${..._PLUGIN_ROOT}`` variables. These are
host-injected, not user-set — a ``link+merge`` install rewrites them to a real path (see
:mod:`~agentry.installers.link_merge`), and the plain-merge path already warns about them
accurately via ``plugin_root_refs``. The generic "set it before your agent runs" advice is
categorically wrong for them, so this scanner stays silent and leaves them to those paths.
"""

from __future__ import annotations

import os
import re

#: ``${VAR}`` or ``${VAR:-default}`` / ``${VAR:default}``. Group 1 is the name, group 2 the
#: (optional) default — a reference *with* a default isn't flagged, it can't be a dead ref.
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:[-=]?[^}]*)?\}")

#: Bare-name form of ``link_merge._PLUGIN_ROOT_RE`` (which matches the full ``${...}`` token).
#: A ``${..._PLUGIN_ROOT}`` var is host-injected — link+merge rewrites it, and the plain-merge
#: path warns via ``plugin_root_refs`` — so the generic dead-placeholder scan must not flag it.
_PLUGIN_ROOT_NAME = re.compile(r"[A-Z0-9_]*PLUGIN_ROOT\Z")


def unset_env_refs(text: str) -> list[str]:
    """Env-var names referenced as ``${NAME}`` (no default) that aren't set in the environment."""
    out: list[str] = []
    for m in _ENV_REF.finditer(text):
        name, default = m.group(1), m.group(2)
        if default is None and name not in os.environ and name not in out:
            if _PLUGIN_ROOT_NAME.match(name):
                continue
            out.append(name)
    return out
