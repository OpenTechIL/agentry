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


def discover(source_root: Path) -> list[Discovered]:
    """List every component a source provides (descriptor if present, else convention)."""
    descriptor = load_descriptor(source_root)
    if descriptor is not None:
        return _discover_from_descriptor(source_root, descriptor)
    return _discover_by_convention(source_root)


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


def index(source_root: Path) -> dict[tuple[ComponentType, str], Path]:
    """Map ``(type, name) -> artifact path`` for one source."""
    return {(d.type, d.name): d.path for d in discover(source_root)}


def requires_for(source_root: Path, ctype: ComponentType, name: str) -> list[Dependency]:
    """The declared dependencies of one component (empty for convention sources)."""
    for d in discover(source_root):
        if d.type is ctype and d.name == name:
            return list(d.requires)
    return []
