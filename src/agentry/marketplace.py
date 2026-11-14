"""Translate a Claude Code plugin marketplace (``.claude-plugin/marketplace.json``) into a catalog.

A marketplace lists plugins, each a git repo (or a subdir of one) in the standard plugin
layout. agentry treats it as just another catalog (ADR-002): :func:`to_index` turns it into
a :class:`~agentry.models.RepositoryIndex` with one :class:`~agentry.models.RepositoryEntry`
per plugin, keyed by the plugin ``name``. Installing a plugin is then an ordinary catalog
``add``: the whole plugin installs through discovery, so T25's plugin.json handling applies.

Pure data, like :mod:`agentry.apm_import`: no I/O. ``base`` says where the marketplace came
from, which decides how relative plugin paths resolve. Entries are validated one at a time,
as Claude does — a bad entry is a warning, never a failed catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from .models import (
    ComponentType,
    ProfileRule,
    RegistrySource,
    RepositoryEntry,
    RepositoryIndex,
    SourceType,
    Strategy,
)

#: Marketplace entry fields that are plugin-manifest component fields. agentry reads a
#: plugin's components from its own files and plugin.json only (v1), so these are reported.
COMPONENT_FIELDS = (
    "commands",
    "agents",
    "skills",
    "hooks",
    "mcpServers",
    "outputStyles",
    "lspServers",
    "workflows",
    "themes",
    "monitors",
    "userConfig",
    "channels",
    "bin",
    "settings",
)
#: Claude-only entry settings agentry has no use for.
IGNORED_FIELDS = ("dependencies", "defaultEnabled", "headers", "headersHelper")
#: Entry source kinds agentry can't install (``command`` would run code).
UNSUPPORTED_SOURCES = ("npm", "archive", "command")
#: v1 installs plugins flat (ADR-002 Q1); only claude commands nest, via the source profile.
NAMESPACED = False
#: A plugin name usable as one folder name (the claude command dir ``<plugin>/``).
SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
#: Where an inline plugin hook's link lives; ``{source}``/``{name}`` expand at install.
HOOK_LINK = ".claude/plugins/agentry/{source}/{name}"

_OWNER_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class MarketBase:
    """Where a marketplace came from, for resolving its relative plugin paths.

    ``git``: a repo ``url`` at ``ref`` (``None`` = the default branch, looked up at add
    time), with the marketplace root at ``root`` inside it (``""`` = the repo root).
    ``local``: a directory ``root`` on disk. ``url``: a bare JSON URL with no known repo.
    """

    kind: Literal["git", "local", "url"]
    root: str = ""
    url: str | None = None
    ref: str | None = None


class _Skip(Exception):
    """An entry agentry can't use; the message is the full warning."""


def is_marketplace(doc: object) -> bool:
    """Content-based detection: a JSON object with a ``plugins`` list and an ``owner`` object."""
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("plugins"), list)
        and isinstance(doc.get("owner"), dict)
    )


def to_index(
    doc: dict, base: MarketBase, catalog: str
) -> tuple[RepositoryIndex, dict[str, list[str]]]:
    """The catalog for marketplace ``doc``, plus warnings keyed by plugin name.

    Warnings not tied to one plugin are keyed ``""``.
    """
    metadata = doc.get("metadata")
    plugin_root = metadata.get("pluginRoot") if isinstance(metadata, dict) else None
    repos: dict[str, RepositoryEntry] = {}
    warnings: dict[str, list[str]] = {}
    for i, raw in enumerate(doc.get("plugins") or []):
        name = raw.get("name") if isinstance(raw, dict) else None
        label = name if isinstance(name, str) and name else f"#{i + 1}"
        notes = warnings.setdefault(label, [])
        try:
            if not isinstance(raw, dict) or not isinstance(name, str) or not name:
                raise _Skip(_invalid(catalog, label, "it has no 'name'"))
            if name in repos:
                raise _Skip(
                    f"catalog '{catalog}': plugin '{name}' is listed more than once in "
                    "marketplace.json — used the first. Ask the marketplace owner to remove "
                    "the duplicate."
                )
            source = _source(raw.get("source"), base, plugin_root, catalog, name)
        except _Skip as skip:
            notes.append(str(skip))
            continue
        notes.extend(_field_notes(raw, catalog, name))
        source = source.model_copy(update={"profiles": plugin_profiles(name), "plugin": name})
        repos[name] = RepositoryEntry(summary=_summary(raw), source=source, namespaced=NAMESPACED)
    return RepositoryIndex(repositories=repos), {k: v for k, v in warnings.items() if v}


def is_safe_segment(name: str) -> bool:
    """Whether ``name`` can be used as one path segment (no separators, dots or tricks)."""
    return bool(SAFE_SEGMENT.fullmatch(name)) and name not in (".", "..")


def plugin_profiles(command_dir: str | None) -> dict[str, dict[ComponentType, ProfileRule]]:
    """The ``profiles:`` an installed plugin's source carries (ADR-002 A, Q1).

    On claude: hooks link the plugin root and merge with ``${CLAUDE_PLUGIN_ROOT}`` rewritten
    under the link; commands nest under ``<command_dir>/`` (``/<plugin>:cmd``) when that is
    a safe folder name. Skills and agents stay flat. Plugin-root MCP servers keep T25's
    skip until ``${CLAUDE_PROJECT_DIR}`` is confirmed in ``.mcp.json`` (step 0).
    """
    rules: dict[ComponentType, ProfileRule] = {
        ComponentType.HOOK: ProfileRule(
            strategy=Strategy.LINK_MERGE,
            dest=HOOK_LINK,
            file=".claude/settings.json",
            pointer="hooks",
            rewrite_from="${CLAUDE_PLUGIN_ROOT}",
            rewrite_to="${CLAUDE_PROJECT_DIR}/" + HOOK_LINK,
            link_from="source_root",
        )
    }
    if command_dir is not None and is_safe_segment(command_dir):
        rules[ComponentType.COMMAND] = ProfileRule(
            strategy=Strategy.LINK, dest=f".claude/commands/{command_dir}/{{name}}.md"
        )
    return {"claude": rules}


# -- helpers --------------------------------------------------------------


def _invalid(catalog: str, plugin: str, why: str) -> str:
    return (
        f"catalog '{catalog}': plugin '{plugin}' is not a valid marketplace entry ({why}) — "
        "skipped. Ask the marketplace owner to fix it."
    )


def _summary(raw: dict) -> str | None:
    desc = raw.get("description") if isinstance(raw.get("description"), str) else None
    version = raw.get("version") if isinstance(raw.get("version"), str) else None
    if version:  # a label only: the lock pins the commit, never the version
        return f"{desc} (v{version})" if desc else f"v{version}"
    return desc


def _article(word: str) -> str:
    """``an`` before a vowel sound (``an npm``, ``an archive``), else ``a``."""
    return "an" if word.lower() == "npm" or word[:1].lower() in "aeiou" else "a"


def _listed(fields: list[str]) -> str:
    names = [f"`{f}`" for f in fields]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _field_notes(raw: dict, catalog: str, name: str) -> list[str]:
    notes: list[str] = []
    component = [k for k in raw if k in COMPONENT_FIELDS]  # marketplace.json order
    if component:
        one = len(component) == 1
        notes.append(
            f"catalog '{catalog}': plugin '{name}' defines {_listed(component)} in "
            "marketplace.json; agentry reads only the plugin's own files and plugin.json — "
            f"{'it is' if one else 'those are'} not installed. Ask the author to move "
            f"{'it' if one else 'them'} into plugin.json."
        )
    ignored = [k for k in raw if k in IGNORED_FIELDS]
    if ignored:
        one = len(ignored) == 1
        notes.append(
            f"catalog '{catalog}': plugin '{name}': {_listed(ignored)} "
            f"{'is a Claude-only setting' if one else 'are Claude-only settings'} — ignored."
        )
    return notes


def _rel_path(catalog: str, name: str, raw: str, plugin_root: object) -> str:
    """A relative plugin path, made relative to the marketplace root."""
    if "\\" in raw:
        raise _Skip(_invalid(catalog, name, f"path {raw!r} uses backslashes"))
    posix = PurePosixPath(raw)
    if not raw.startswith("./") and isinstance(plugin_root, str) and plugin_root:
        posix = PurePosixPath(plugin_root) / posix  # a bare name lives under pluginRoot
    if posix.is_absolute() or ".." in posix.parts:
        raise _Skip(_invalid(catalog, name, f"path {raw!r} leaves the marketplace"))
    rel = posix.as_posix().removeprefix("./").strip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    return "/".join(parts)


def _git_url(value: str) -> str:
    """``owner/repo`` shorthand → a GitHub URL; anything else as given."""
    return f"https://github.com/{value}" if _OWNER_REPO.fullmatch(value) else value


def _pin(spec: dict) -> tuple[str, bool, bool]:
    """``(ref, pinned, default_branch)``: ``sha`` beats ``ref``, as in Claude."""
    sha, ref = spec.get("sha"), spec.get("ref")
    if isinstance(sha, str) and sha:
        return sha, True, False
    if isinstance(ref, str) and ref:
        return ref, False, False
    return "main", False, True


def _source(
    value: object, base: MarketBase, plugin_root: object, catalog: str, name: str
) -> RegistrySource:
    if isinstance(value, str):
        rel = _rel_path(catalog, name, value, plugin_root)
        if base.kind == "local":
            path = Path(base.root) / rel if rel else Path(base.root)
            return RegistrySource(type=SourceType.LOCAL, path=str(path))
        if base.kind == "git" and base.url:
            subdir = "/".join(p for p in (base.root, rel) if p) or None
            return RegistrySource(
                type=SourceType.GIT,
                url=base.url,
                ref=base.ref or "main",
                subdir=subdir,
                default_branch=base.ref is None,
            )
        raise _Skip(
            f"catalog '{catalog}': plugin '{name}' uses a relative path, but the marketplace "
            "was added from a URL with no repo — skipped. Add the marketplace by its repo URL "
            "or a local checkout."
        )
    if not isinstance(value, dict):
        raise _Skip(_invalid(catalog, name, "its 'source' is missing"))
    kind = value.get("source")
    if kind in UNSUPPORTED_SOURCES:
        raise _Skip(
            f"catalog '{catalog}': plugin '{name}' uses {_article(kind)} {kind} source, which "
            "agentry can't install — skipped. Install it in Claude Code directly."
        )
    ref, pinned, default = _pin(value)
    if kind == "github":
        repo = value.get("repo")
        if not isinstance(repo, str) or not _OWNER_REPO.fullmatch(repo):
            raise _Skip(_invalid(catalog, name, "a github source needs 'repo': 'owner/name'"))
        url, subdir = f"https://github.com/{repo}", None
    elif kind == "url":
        raw_url = value.get("url")
        if not isinstance(raw_url, str) or not raw_url:
            raise _Skip(_invalid(catalog, name, "a url source needs 'url'"))
        url, subdir = raw_url, None
    elif kind == "git-subdir":
        raw_url, sub = value.get("url"), value.get("path")
        if not isinstance(raw_url, str) or not raw_url or not isinstance(sub, str):
            raise _Skip(_invalid(catalog, name, "a git-subdir source needs 'url' and 'path'"))
        url = _git_url(raw_url)
        subdir = _rel_path(catalog, name, sub, None) or None
    else:
        raise _Skip(_invalid(catalog, name, f"unknown source type {kind!r}"))
    return RegistrySource(
        type=SourceType.GIT,
        url=url,
        ref=ref,
        subdir=subdir,
        pinned=pinned,
        default_branch=default,
    )
