"""Read a Claude Code plugin manifest (``.claude-plugin/plugin.json``) into agentry terms.

A plugin repo uses the standard layout agentry's convention scan already understands
(``skills/``, ``commands/``, ``agents/``, ``hooks/hooks.json``, ``.mcp.json``), but its
``plugin.json`` can redirect or extend it. This module translates those overrides into a
:class:`PluginLayout`: a :class:`~agentry.models.SourceDescriptor` of the components it
adds, plus the default scans it *replaces*. Discovery then stays layout-agnostic: it
merges that with the convention scan, the same way an ``agentry.yaml`` descriptor is read.

Semantics follow Claude Code's plugin loader:

* ``commands`` / ``agents`` **replace** the default ``commands/`` / ``agents/`` scan.
* ``skills`` **adds** to ``skills/``. Each path is a skills root (``<name>/`` children) or
  is itself a skill directory.
* ``hooks`` / ``mcpServers`` **add** to ``hooks/hooks.json`` / ``.mcp.json``. Each may be a
  path, a list of paths, or an inline object. An inline object becomes one component
  (``plugin-hooks`` / ``plugin-mcp``) whose artifact is ``plugin.json`` itself; merging
  picks the ``hooks`` / ``mcpServers`` key out of it like any wrapped fragment.

Every path must stay inside the **source** (a marketplace's sibling plugins may be
symlinked); anything else is skipped with a warning. Nothing here writes to disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from .config import STORE_DIR
from .models import ComponentType, ProvidesEntry, SourceDescriptor

MANIFEST_PATH = ".claude-plugin/plugin.json"
INLINE_HOOKS = "plugin-hooks"
INLINE_MCP = "plugin-mcp"

#: plugin.json keys with no agentry component type (installed by Claude only).
UNSUPPORTED_KEYS = (
    "outputStyles",
    "workflows",
    "themes",
    "lspServers",
    "monitors",
    "userConfig",
    "channels",
    "bin",
)
#: Each path key's default location, as the example in a rejected-path hint.
_EXAMPLE_PATHS = {
    "commands": "./commands",
    "agents": "./agents",
    "skills": "./skills",
    "hooks": "./hooks/hooks.json",
    "mcpServers": "./.mcp.json",
}
_BUNDLE_SUFFIXES = (".mcpb", ".dxt")
_PLUGIN_DATA = ("${CLAUDE_PLUGIN_DATA}", "$CLAUDE_PLUGIN_DATA")


@dataclass
class PluginLayout:
    """What a plugin.json says about its components, in agentry terms."""

    name: str
    descriptor: SourceDescriptor
    #: Component types whose default convention scan the manifest replaces.
    replaces: frozenset[ComponentType] = frozenset()
    warnings: list[str] = field(default_factory=list)
    #: The subset of ``warnings`` about keys agentry can't install (summarized by sync).
    unsupported: list[str] = field(default_factory=list)


def source_label(root: Path) -> str:
    """The source's name, for warnings: the store directory the root lives in."""
    for p in [root, *root.parents]:
        if p.parent.name == STORE_DIR:
            return p.name
    return root.name


def confinement_root(root: Path) -> Path:
    """The real directory every plugin path must stay inside: the whole source, so a
    marketplace plugin may symlink a sibling plugin's files.

    Inside agentry's store that is ``.agentry/<source>`` (resolved, so a local source's
    real directory). Outside a store (a plugin checked out on its own), the nearest
    ancestor that is a repo or marketplace root (``.git`` or
    ``.claude-plugin/marketplace.json``), else ``root`` itself.
    """
    for p in [root, *root.parents]:
        if p.parent.name == STORE_DIR:
            return Path(os.path.realpath(p))
    for p in [root, *root.parents]:
        if (p / ".git").exists() or (p / ".claude-plugin" / "marketplace.json").is_file():
            return Path(os.path.realpath(p))
    return Path(os.path.realpath(root))


def load(root: Path, src: str | None = None) -> PluginLayout | None:
    """Translate ``<root>/.claude-plugin/plugin.json``; ``None`` if there is none.

    A malformed manifest yields an empty layout carrying a warning, so discovery falls
    back to the plain convention scan. ``src`` names the source in warnings (default: the
    store directory the root lives in, see :func:`source_label`).
    """
    path = root / MANIFEST_PATH
    if not path.is_file():
        return None
    src = src or source_label(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise ValueError("it must be a JSON object with a string 'name'")
    except (ValueError, OSError) as exc:
        return PluginLayout(
            name="",
            descriptor=SourceDescriptor(),
            warnings=[
                f"source '{src}': {MANIFEST_PATH} is not valid ({exc}) — ignored it and used "
                "the convention layout (skills/, commands/, agents/, hooks/, .mcp.json). Fix the "
                "file, or ask the plugin author to."
            ],
        )
    return _Translator(root, src, data).run()


class _Translator:
    def __init__(self, root: Path, src: str, data: dict) -> None:
        self.root = root
        self.src = src
        self.data = data
        self.confine = confinement_root(root)
        self.provides: dict[ComponentType, list[ProvidesEntry]] = {}
        self.replaces: set[ComponentType] = set()
        self.warnings: list[str] = []

    def run(self) -> PluginLayout:
        self._files(ComponentType.COMMAND, "commands", replace=True)
        self._files(ComponentType.AGENT, "agents", replace=True)
        self._skills()
        self._fragments(ComponentType.HOOK, "hooks", INLINE_HOOKS)
        self._fragments(ComponentType.MCP, "mcpServers", INLINE_MCP)
        keys = [k for k in self.data if k in UNSUPPORTED_KEYS]  # plugin.json order
        unsupported = []
        if keys:
            names = [f"`{k}`" for k in keys]
            listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
            verb = "has" if len(names) == 1 else "have"
            unsupported.append(
                f"source '{self.src}': plugin.json {listed} {verb} no agentry equivalent — "
                f"skipped. {'It still works' if len(names) == 1 else 'They still work'} when "
                "the plugin is installed in Claude Code itself."
            )
        return PluginLayout(
            name=self.data["name"],
            descriptor=SourceDescriptor(provides=self.provides),
            replaces=frozenset(self.replaces),
            warnings=[*self.warnings, *unsupported],
            unsupported=unsupported,
        )

    # -- helpers --------------------------------------------------------

    def _add(self, ctype: ComponentType, name: str, rel: str) -> None:
        self.provides.setdefault(ctype, []).append(ProvidesEntry(name=name, path=rel))

    def _resolve(self, key: str, raw: object) -> Path | None:
        """A manifest path, checked: relative, forward slashes, inside the source."""
        if not isinstance(raw, str) or not raw.strip():
            inline = " or an inline object" if key in ("hooks", "mcpServers") else ""
            self.warnings.append(
                f"source '{self.src}': plugin.json '{key}' value {json.dumps(raw)} is neither a "
                "path nor an object — skipped it. Use a relative path inside the plugin (e.g. "
                f"'{_EXAMPLE_PATHS.get(key, './commands')}'){inline}."
            )
            return None
        if "\\" in raw:
            self._reject(key, raw, "uses backslashes")
            return None
        posix = PurePosixPath(raw)
        if posix.is_absolute() or PureWindowsPath(raw).drive:
            self._reject(key, raw, "is absolute")
            return None
        if ".." in posix.parts:
            self._reject(key, raw, "leaves the plugin (..)")
            return None
        candidate = self.root / posix
        real = Path(os.path.realpath(candidate))
        if real != self.confine and self.confine not in real.parents:
            self._reject(key, raw, "resolves outside the source (symlink)")
            return None
        if not candidate.exists():
            self.warnings.append(
                f"source '{self.src}': plugin.json '{key}' path '{raw}' does not exist — "
                "skipped it. Fix the path in plugin.json."
            )
            return None
        return candidate

    def _reject(self, key: str, raw: str, why: str) -> None:
        example = _EXAMPLE_PATHS.get(key, "./commands")
        self.warnings.append(
            f"source '{self.src}': plugin.json '{key}' path '{raw}' {why} — skipped it. Use a "
            f"relative path inside the plugin, e.g. '{example}'."
        )

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    @staticmethod
    def _paths(value: object) -> list[object]:
        return list(value) if isinstance(value, list) else [value]

    # -- per key --------------------------------------------------------

    def _files(self, ctype: ComponentType, key: str, *, replace: bool) -> None:
        """commands/agents: ``*.md`` files or directories of them; replaces the default."""
        value = self.data.get(key)
        if value is None:
            return
        if replace:
            self.replaces.add(ctype)
        if isinstance(value, dict):  # commands map form: {name: {source | content}}
            for name, spec in value.items():
                source = spec.get("source") if isinstance(spec, dict) else None
                if source is None:
                    self.warnings.append(
                        f"source '{self.src}': plugin.json {key} '{name}' is inline content, "
                        "which agentry can't install as a file — skipped it. Ship it as the file "
                        f"{key}/{name}.md instead."
                    )
                    continue
                p = self._resolve(key, source)
                if p is not None and p.is_file():
                    self._add(ctype, str(name), self._rel(p))
            return
        for raw in self._paths(value):
            p = self._resolve(key, raw)
            if p is None:
                continue
            files = sorted(p.glob("*.md")) if p.is_dir() else [p]
            for f in files:
                if f.is_file() and f.suffix == ".md":
                    self._add(ctype, f.stem, self._rel(f))

    def _skills(self) -> None:
        """skills: each path is a skill dir (has SKILL.md) or a root of skill dirs; adds."""
        value = self.data.get("skills")
        if value is None:
            return
        for raw in self._paths(value):
            p = self._resolve("skills", raw)
            if p is None or not p.is_dir():
                continue
            if (p / "SKILL.md").is_file():
                self._add(ComponentType.SKILL, p.name, self._rel(p))
                continue
            for child in sorted(p.iterdir()):
                if child.is_dir() and (child / "SKILL.md").is_file():
                    self._add(ComponentType.SKILL, child.name, self._rel(child))

    def _fragments(self, ctype: ComponentType, key: str, inline_name: str) -> None:
        """hooks/mcpServers: an inline object, or path(s) to JSON fragments; adds."""
        value = self.data.get(key)
        if value is None:
            return
        if isinstance(value, dict):
            self._add(ctype, inline_name, MANIFEST_PATH)
            return
        for raw in self._paths(value):
            if isinstance(raw, dict):  # an inline object inside a list
                self._add(ctype, inline_name, MANIFEST_PATH)
                continue
            if isinstance(raw, str) and raw.endswith(_BUNDLE_SUFFIXES):
                self.warnings.append(
                    f"source '{self.src}': plugin.json {key} '{raw}' is an MCP bundle, which "
                    "agentry can't install — skipped it. Ask the plugin author for a JSON "
                    "mcpServers entry."
                )
                continue
            p = self._resolve(key, raw)
            if p is None or not p.is_file():
                continue
            if ctype is ComponentType.MCP and p.name in (".mcp.json", "mcp.json"):
                continue  # the root .mcp.json the convention scan already provides
            self._add(ctype, p.stem, self._rel(p))


def manifest_key(ctype: ComponentType) -> str:
    """The plugin.json key an inline hook/MCP component's entries live under."""
    return "hooks" if ctype is ComponentType.HOOK else "mcpServers"


def is_manifest(artifact: Path) -> bool:
    """Whether ``artifact`` is a plugin's ``.claude-plugin/plugin.json`` (an inline component)."""
    return artifact.name == "plugin.json" and artifact.parent.name == ".claude-plugin"


def origin(artifact: Path, ctype: ComponentType) -> str | None:
    """``.claude-plugin/plugin.json (hooks)`` for an inline component, else ``None``."""
    return f"{MANIFEST_PATH} ({manifest_key(ctype)})" if is_manifest(artifact) else None


def merge_text(artifact: Path, ctype: ComponentType) -> str:
    """The text agentry installs from a hook/MCP artifact, for placeholder scans.

    For plugin.json that is only the ``hooks`` / ``mcpServers`` subtree, so ``${VAR}`` text in
    fields agentry doesn't install (``description``, ``userConfig``, …) is not reported.
    Raises ``OSError`` / ``ValueError`` like reading the file would.
    """
    text = artifact.read_text(encoding="utf-8")
    if not is_manifest(artifact):
        return text
    data = json.loads(text)
    return json.dumps(data.get(manifest_key(ctype), {}) if isinstance(data, dict) else {})


def manifest_entries(artifact: Path, ctype: ComponentType) -> tuple[dict, list[str]]:
    """The named entries an inline plugin.json component merges, plus notes on what was left out.

    Only ``data[manifest_key(ctype)]`` is read — never the rest of plugin.json:

    * an object: its entries (a hooks value may be hooks.json-style ``{"hooks": {...}}``);
    * a list: the entries of each object in it, in order (string items are paths, installed
      as their own components); for MCP, the first object to name a server wins; for hooks,
      the groups for one event are concatenated;
    * anything else: nothing.

    Notes are clauses ("…") the caller prefixes with the component ref. Raises ``OSError`` /
    ``ValueError`` for an unreadable or malformed file.
    """
    data = json.loads(artifact.read_text(encoding="utf-8"))
    key = manifest_key(ctype)
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, dict):
        return _unwrap_hooks(value, ctype), []
    if not isinstance(value, list):
        what = "is missing" if value is None else f"is {_json_kind(value)}, not an object"
        return {}, [
            f"plugin.json '{key}' {what} — merged nothing. Make it an object or a list of "
            "objects in plugin.json, or ask the plugin author to."
        ]
    entries: dict = {}
    notes: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue  # a path: discovery made it its own component
        for name, entry in _unwrap_hooks(item, ctype).items():
            if name not in entries:
                entries[name] = entry
            elif ctype is ComponentType.HOOK and isinstance(entries[name], list):
                entries[name] = [*entries[name], *(entry if isinstance(entry, list) else [entry])]
            else:
                notes.append(
                    f"plugin.json '{key}' lists '{name}' more than once — merged the first one "
                    "and skipped the rest. Remove the duplicate from plugin.json."
                )
    return entries, notes


def _json_kind(value: object) -> str:
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "a number"
    return "a string" if isinstance(value, str) else "null"


def _unwrap_hooks(value: dict, ctype: ComponentType) -> dict:
    """A hooks value may itself be hooks.json-style: ``{"hooks": {<event>: [...]}}``."""
    inner = value.get("hooks")
    if ctype is ComponentType.HOOK and isinstance(inner, dict):
        return inner
    return value


def plugin_data_refs(ref: str, ctype: ComponentType, artifact: Path) -> list[str]:
    """Warnings for a hook/MCP artifact that references ``${CLAUDE_PLUGIN_DATA}``.

    For the inline components only the ``hooks`` / ``mcpServers`` subtree is scanned, so a
    mention elsewhere in plugin.json doesn't trigger it.
    """
    try:
        text = merge_text(artifact, ctype)
    except (ValueError, OSError):
        return []
    if "CLAUDE_PLUGIN_DATA" in os.environ or not any(m in text for m in _PLUGIN_DATA):
        return []
    return [
        f"{ref}: references ${{CLAUDE_PLUGIN_DATA}}, which only exists inside an installed "
        "Claude plugin — installed as-is, but it won't resolve. Set CLAUDE_PLUGIN_DATA in your "
        "environment, or ask the plugin author for a fallback."
    ]
