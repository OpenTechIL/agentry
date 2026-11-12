"""Discover installable components inside a downloaded source.

Two modes:

* **Descriptor** — if a source root contains ``agentry.yaml`` (or ``.yml``), it
  self-describes its components via ``provides`` (explicit ``path`` or ``glob``).
* **Convention** — otherwise, scan the standard agent layout::

      skills/<name>/        (directory, usually containing SKILL.md)
      agents/<name>.md      (file)
      commands/<name>.md    (file)
      tools/<name>/         (directory)
      hooks/<name>.json     (JSON fragment merged into the target's settings)
      mcp/<name>.json       (JSON MCP-server entry merged into the target's config)

The component *type* dictates shape (dir vs file + extension); a descriptor only
needs to say *where*.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from . import plugin_manifest
from .models import (
    KNOWN_HARNESS_SLUGS,
    MERGE_TYPES,
    TYPE_EXT,
    TYPE_IS_DIR,
    ComponentType,
    Dependency,
    SourceDescriptor,
)

DESCRIPTOR_NAMES = ("agentry.yaml", "agentry.yml")


@dataclass(frozen=True)
class Layout:
    subdir: str
    is_dir: bool
    ext: str = ""


#: Convention scan locations (subdir per type); shape comes from TYPE_IS_DIR/TYPE_EXT.
LAYOUT: dict[ComponentType, Layout] = {
    ctype: Layout(ctype.value + "s", TYPE_IS_DIR[ctype], TYPE_EXT.get(ctype, ""))
    for ctype in ComponentType
}
# `mcp` pluralizes oddly; keep its dir as `mcp`.
LAYOUT[ComponentType.MCP] = Layout("mcp", False, ".json")


@dataclass(frozen=True)
class Discovered:
    type: ComponentType
    name: str
    path: Path  # absolute path to the artifact in the source
    requires: tuple[Dependency, ...] = ()  # components this one depends on (descriptor only)
    #: For merge fragments (hooks/mcp), the AI harness a per-harness variant targets,
    #: derived from a ``<base>-<harness>`` filename (e.g. ``hooks-cursor`` -> ``cursor``).
    #: ``None`` for the canonical/suffixless fragment and all link-based component types.
    harness: str | None = None


def artifact_path(source_root: Path, ctype: ComponentType, name: str) -> Path:
    """Convention location of a component inside a source root."""
    layout = LAYOUT[ctype]
    base = source_root / layout.subdir
    return base / name if layout.is_dir else base / f"{name}{layout.ext}"


def load_descriptor(source_root: Path) -> SourceDescriptor | None:
    """Read ``agentry.yaml`` from a source root, if present."""
    for fname in DESCRIPTOR_NAMES:
        path = source_root / fname
        if path.is_file():
            data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
            return SourceDescriptor.model_validate(data)
    return None


#: Microsoft apm packages keep their primitives under a ``.apm/`` tree with apm's own dir
#: names and compound extensions. Map the ones with an agentry equivalent: apm ``prompts``
#: are agentry ``commands``; apm ``instructions`` have no agentry component type (a repo-wide
#: doc, not a placed artifact) and are skipped. The value is ``(type, suffix)`` — a ``None``
#: suffix means a directory component (skills), else the filename suffix to strip for the name.
APM_PACKAGE_DIR = ".apm"
APM_LAYOUT: dict[str, tuple[ComponentType, str | None]] = {
    "skills": (ComponentType.SKILL, None),
    "agents": (ComponentType.AGENT, ".agent.md"),
    "prompts": (ComponentType.COMMAND, ".prompt.md"),
}


def discover(source_root: Path) -> list[Discovered]:
    """List every component a source provides.

    Precedence: an explicit ``agentry.yaml`` descriptor; then a Claude plugin's
    ``.claude-plugin/plugin.json`` (applied on top of the convention scan); then a Microsoft
    apm ``.apm/`` package tree (consumed directly, no republishing); then agentry's own
    convention scan.
    """
    descriptor = load_descriptor(source_root)
    if descriptor is not None:
        return _discover_from_descriptor(source_root, descriptor)
    layout = plugin_manifest.load(source_root)
    if layout is not None:
        return _discover_plugin(source_root, layout)[0]
    if (source_root / APM_PACKAGE_DIR).is_dir():
        return _discover_apm_package(source_root)
    return _discover_by_convention(source_root)


def _discover_apm_package(source_root: Path) -> list[Discovered]:
    """Discover an apm package's primitives under ``.apm/`` and map them to agentry types.

    A ``(type, name)`` is resolved by reconcile via this index, so the artifact keeps its
    real ``.apm/`` path (e.g. ``design-reviewer.agent.md``) while installing under agentry's
    own naming (``.claude/agents/design-reviewer.md``). apm ``instructions`` are skipped.
    """
    apm = source_root / APM_PACKAGE_DIR
    found: list[Discovered] = []
    for subdir, (ctype, suffix) in APM_LAYOUT.items():
        base = apm / subdir
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if suffix is None:
                if entry.is_dir():
                    found.append(Discovered(ctype, entry.name, entry))
            elif entry.is_file() and entry.name.endswith(suffix):
                found.append(Discovered(ctype, entry.name[: -len(suffix)], entry))
    return found


def _name_for(ctype: ComponentType, path: Path) -> str:
    return path.name if TYPE_IS_DIR[ctype] else path.stem


def harness_suffix(name: str) -> str | None:
    """The AI harness a ``<base>-<harness>`` fragment name targets, else ``None``.

    Only the hyphenated form counts, so a bare ``codex`` (e.g. an MCP server literally
    named "codex") is not misread as a per-harness variant.
    """
    base, sep, suffix = name.rpartition("-")
    if sep and base and suffix in KNOWN_HARNESS_SLUGS:
        return suffix
    return None


def _harness_for(ctype: ComponentType, name: str) -> str | None:
    """Harness affinity for a discovered component (merge fragments only)."""
    return harness_suffix(name) if ctype in MERGE_TYPES else None


def _discover_from_descriptor(source_root: Path, descriptor: SourceDescriptor) -> list[Discovered]:
    found: list[Discovered] = []
    seen: set[tuple[ComponentType, str]] = set()
    for ctype, entries in descriptor.provides.items():
        for entry in entries:
            requires = tuple(entry.requires)
            if entry.path:
                p = source_root / entry.path
                if not p.exists():
                    continue
                name = entry.name or _name_for(ctype, p)
                if (ctype, name) not in seen:
                    found.append(Discovered(ctype, name, p, requires, _harness_for(ctype, name)))
                    seen.add((ctype, name))
            elif entry.glob:
                for match in sorted(source_root.glob(entry.glob)):
                    name = _name_for(ctype, match)
                    if (ctype, name) not in seen:
                        found.append(
                            Discovered(ctype, name, match, requires, _harness_for(ctype, name))
                        )
                        seen.add((ctype, name))
    return found


#: Root-level MCP files (e.g. a Claude Code plugin's ``.mcp.json``) surfaced as a single
#: ``mcp`` component, in preference order. Many plugins ship one ``{"mcpServers": {...}}``
#: file at the (subdir-adjusted) source root rather than per-server ``mcp/<name>.json``.
ROOT_MCP_NAMES = (".mcp.json", "mcp.json")
ROOT_MCP_COMPONENT = "mcp"


def _discover_by_convention(source_root: Path) -> list[Discovered]:
    found: list[Discovered] = []
    seen: set[tuple[ComponentType, str]] = set()
    for ctype, layout in LAYOUT.items():
        base = source_root / layout.subdir
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if layout.is_dir:
                if entry.is_dir():
                    found.append(Discovered(ctype, entry.name, entry))
                    seen.add((ctype, entry.name))
            elif entry.is_file() and entry.suffix == layout.ext:
                found.append(
                    Discovered(ctype, entry.stem, entry, harness=_harness_for(ctype, entry.stem))
                )
                seen.add((ctype, entry.stem))

    # A root-level `.mcp.json` (plugin convention) → one `mcp` component, unless an
    # `mcp/mcp.json` already claimed that name above.
    key = (ComponentType.MCP, ROOT_MCP_COMPONENT)
    if key not in seen:
        for fname in ROOT_MCP_NAMES:
            root_mcp = source_root / fname
            if root_mcp.is_file():
                found.append(Discovered(ComponentType.MCP, ROOT_MCP_COMPONENT, root_mcp))
                break
    return found


def _discover_plugin(
    source_root: Path, layout: plugin_manifest.PluginLayout, src: str | None = None
) -> tuple[list[Discovered], list[str]]:
    """The convention scan, minus the scans plugin.json replaces, plus what it adds.

    A file-based component wins a name clash with a manifest one (e.g. a real
    ``hooks/plugin-hooks.json`` over inline ``hooks``); the clash is reported.
    """
    found = [d for d in _discover_by_convention(source_root) if d.type not in layout.replaces]
    seen = {(d.type, d.name): d for d in found}
    warnings: list[str] = []
    src = src or plugin_manifest.source_label(source_root)
    for d in _discover_from_descriptor(source_root, layout.descriptor):
        prior = seen.get((d.type, d.name))
        if prior is None:
            found.append(d)
            seen[(d.type, d.name)] = d
        elif prior.path != d.path:
            warnings.append(
                f"source '{src}': plugin.json would add {d.type.value} '{d.name}', but "
                f"{prior.path.relative_to(source_root).as_posix()} already provides it — "
                "installed the file one; the plugin.json one is not installed. Rename the "
                "file to install both."
            )
    return found, warnings


def plugin_source_warnings(
    source_root: Path, src: str | None = None
) -> tuple[list[str], list[str]]:
    """Source-level findings about a source's plugin.json: ``(all, unsupported)``.

    ``all`` covers rejected or missing paths, name clashes and unsupported keys;
    ``unsupported`` is the unsupported-key subset (sync summarizes those). Both are empty
    when the source has no plugin.json or an ``agentry.yaml`` descriptor wins.
    """
    if load_descriptor(source_root) is not None:
        return [], []  # an agentry.yaml wins entirely; plugin.json isn't read
    layout = plugin_manifest.load(source_root, src)
    if layout is None:
        return [], []
    _, clashes = _discover_plugin(source_root, layout, src)
    return [*layout.warnings, *clashes], list(layout.unsupported)


def plugin_warnings(source_root: Path) -> list[str]:
    """Everything worth telling the user about a source's plugin.json (empty if none):
    rejected or missing paths, unsupported keys, name clashes, ``${CLAUDE_PLUGIN_DATA}``."""
    out, _ = plugin_source_warnings(source_root)
    if load_descriptor(source_root) is not None or plugin_manifest.load(source_root) is None:
        return out
    src = plugin_manifest.source_label(source_root)
    for d in discover(source_root):
        if d.type in MERGE_TYPES:
            ref = f"{src}/{d.type.value}/{d.name}"
            out.extend(plugin_manifest.plugin_data_refs(ref, d.type, d.path))
    return out


def index(source_root: Path) -> dict[tuple[ComponentType, str], Path]:
    """Map ``(type, name) -> artifact path`` for one source."""
    return {(d.type, d.name): d.path for d in discover(source_root)}


def requires_for(source_root: Path, ctype: ComponentType, name: str) -> list[Dependency]:
    """The declared dependencies of one component (empty for convention sources)."""
    for d in discover(source_root):
        if d.type is ctype and d.name == name:
            return list(d.requires)
    return []
