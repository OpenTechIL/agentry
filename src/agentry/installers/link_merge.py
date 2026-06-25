"""Link+merge strategy — symlink a script directory AND merge its config, with rewrite.

Some plugins (e.g. a Claude Code hooks bundle) ship a directory of scripts plus a
config file whose entries invoke those scripts by a plugin-relative path that only
expands inside a real installed plugin (``${CLAUDE_PLUGIN_ROOT}/hooks/x.mjs``). A plain
config merge would therefore install dead commands. This strategy:

1. symlinks the script directory into the target tool's layout (reusing the safe
   :mod:`~agentry.installers.link` primitives), and
2. merges the config fragment (unwrapped like any merge fragment), after **rewriting**
   the command-path prefix so it points at the symlinked location.

Removal undoes both halves; the manifest records the link path and the owned merge keys.
"""

from __future__ import annotations

from ..targets import LinkMergeDest


def _rewrite_strings(value, frm: str, to: str):
    """Deep-copy ``value`` replacing every occurrence of ``frm`` with ``to`` in strings."""
    if isinstance(value, str):
        return value.replace(frm, to) if frm else value
    if isinstance(value, list):
        return [_rewrite_strings(v, frm, to) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite_strings(v, frm, to) for k, v in value.items()}
    return value


def _plugin_var(rewrite_from: str) -> str | None:
    """The leading ``${VAR}`` token of a rewrite prefix, if any (for leftover detection)."""
    if rewrite_from.startswith("${"):
        end = rewrite_from.find("}")
        if end != -1:
            return rewrite_from[: end + 1]
    return None


def _collect_leftovers(value, token: str, out: list[str]) -> None:
    """Gather string values still containing ``token`` after rewriting (likely dead paths)."""
    if isinstance(value, str):
        if token in value:
            out.append(value)
    elif isinstance(value, list):
        for v in value:
            _collect_leftovers(v, token, out)
    elif isinstance(value, dict):
        for v in value.values():
            _collect_leftovers(v, token, out)


def rewrite_fragment(fragment: dict, dest: LinkMergeDest, name: str) -> tuple[dict, list[str]]:
    """Rewrite command paths in ``fragment`` and report any still-unresolved references.

    Returns ``(rewritten_fragment, leftover_strings)``. ``leftover_strings`` are command
    strings that still reference the original plugin variable after rewriting — they will
    not resolve from the merged config and should be surfaced as a warning.
    """
    if not dest.rewrite_from:
        return fragment, []
    # Substitute {name} literally — rewrite_to often contains other ${...} shell vars
    # (e.g. ${CLAUDE_PROJECT_DIR}) that str.format would misread as fields.
    to = dest.rewrite_to.replace("{name}", name)
    rewritten = _rewrite_strings(fragment, dest.rewrite_from, to)
    leftovers: list[str] = []
    token = _plugin_var(dest.rewrite_from)
    if token:
        _collect_leftovers(rewritten, token, leftovers)
    return rewritten, leftovers
