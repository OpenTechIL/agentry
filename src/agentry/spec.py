"""Capability-map dataclasses — the *target side* of an install.

A :class:`TargetSpec` declares, per :class:`~agentry.models.ComponentType`, how a
component installs into one AI tool: a **link**/**copy** destination (path template,
``{name}`` substituted), a **merge** destination (a JSON config file + the top-level key
entries live under), or a composite **link+merge**.

These live in their own leaf module (importing only :mod:`agentry.models`) so both
:mod:`agentry.targets` and the per-agent :mod:`agentry.drivers` modules can import them
without an import cycle. :mod:`agentry.targets` re-exports them for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import ComponentType, Strategy


@dataclass(frozen=True)
class MergeDest:
    file: str  # config file path relative to project root
    pointer: str  # top-level JSON key entries are merged under
    #: Extra top-level keys (besides ``pointer``) a *source* fragment may wrap its
    #: named entries under, to be unwrapped on merge. Plugin/MCP files in the wild
    #: often ship entries wrapped under the section name they target — usually the
    #: same as ``pointer`` (handled implicitly), but e.g. an OpenCode ``mcp`` config
    #: may carry the Claude-style ``mcpServers`` wrapper.
    aliases: tuple[str, ...] = ()

    @property
    def wrapper_keys(self) -> tuple[str, ...]:
        """Top-level keys that, in a source fragment, wrap the real named entries."""
        return (self.pointer, *self.aliases)


@dataclass(frozen=True)
class LinkMergeDest:
    """Composite install: symlink a script dir AND merge its config, rewriting paths.

    ``link_dest`` is a symlink destination template (``{name}`` placeholder) for the
    script directory; ``merge`` says where the config entries go. ``rewrite_from`` /
    ``rewrite_to`` (both optional, ``{name}`` expands in ``rewrite_to``) rewrite a
    command-path prefix in the merged fragment so the symlinked scripts resolve from
    the target tool — e.g. a Claude plugin's ``${CLAUDE_PLUGIN_ROOT}/hooks`` →
    ``${CLAUDE_PROJECT_DIR}/.claude/hooks/{name}``.
    """

    link_dest: str
    merge: MergeDest
    rewrite_from: str = ""
    rewrite_to: str = ""


@dataclass(frozen=True)
class TargetSpec:
    name: str
    #: component type -> symlink destination template (``{name}`` placeholder)
    link: dict[ComponentType, str] = field(default_factory=dict)
    #: component type -> copy destination template (``{name}`` placeholder)
    copy: dict[ComponentType, str] = field(default_factory=dict)
    #: component type -> config-merge destination
    merge: dict[ComponentType, MergeDest] = field(default_factory=dict)
    #: component type -> composite link+merge destination
    link_merge: dict[ComponentType, LinkMergeDest] = field(default_factory=dict)

    def supports(self, ctype: ComponentType) -> bool:
        return (
            ctype in self.link
            or ctype in self.copy
            or ctype in self.merge
            or ctype in self.link_merge
        )

    def strategy(self, ctype: ComponentType) -> Strategy | None:
        if ctype in self.link_merge:
            return Strategy.LINK_MERGE
        if ctype in self.copy:
            return Strategy.COPY
        if ctype in self.link:
            return Strategy.LINK
        if ctype in self.merge:
            return Strategy.MERGE
        return None

    def link_dest(self, ctype: ComponentType, name: str) -> str:
        return self.link[ctype].format(name=name)

    def copy_dest(self, ctype: ComponentType, name: str) -> str:
        return self.copy[ctype].format(name=name)

    def merge_dest(self, ctype: ComponentType) -> MergeDest:
        return self.merge[ctype]

    def link_merge_dest(self, ctype: ComponentType) -> LinkMergeDest:
        return self.link_merge[ctype]
