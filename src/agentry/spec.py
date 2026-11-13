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
from pathlib import Path

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
class FormatExpectation:
    """What a target needs from one component type's *content* (not just its placement).

    Phase A of the cross-target format check: agentry still installs the bytes as-is, but
    warns (and ``doctor --strict`` fails) when an artifact or merged entry visibly won't work
    in the target — so the mismatch is reported at install time instead of silently. All
    rules are data, declared on a driver's :class:`TargetSpec` or a ``target_profiles``
    rule's ``expect:``; the engine never branches on a tool name.
    """

    #: The artifact file must have this suffix (e.g. ``".toml"``). Directories are skipped.
    extension: str | None = None
    #: Merged keys must be one of these (e.g. a tool's hook event names).
    events: frozenset[str] | None = None
    #: Every merged entry must have these fields.
    require: tuple[str, ...] = ()
    #: field -> JSON type a merged entry's field must have when present
    #: (``"array"``, ``"object"``, ``"string"``, ``"number"``, ``"boolean"``).
    types: dict[str, str] = field(default_factory=dict)
    #: Fields a merged entry must not carry (keys the target's schema rejects).
    forbid: tuple[str, ...] = ()
    #: field -> allowed values for a merged entry's field when present.
    values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: field -> values the target misreads (e.g. Gemini treats ``type: "http"`` + ``url`` as
    #: SSE); flagged with ``reject_hint``.
    reject: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Why a ``reject`` value is wrong and what to use instead (appended to the problem).
    reject_hint: str = ""
    #: Human summary of what the target wants, quoted in the warning.
    expects: str = ""


_JSON_TYPES = {
    "array": list,
    "object": dict,
    "string": str,
    "boolean": bool,
}


def _json_type_of(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    for name, py in _JSON_TYPES.items():
        if isinstance(value, py):
            return name
    return "null" if value is None else type(value).__name__


def _a(word: str) -> str:
    return f"{'an' if word[:1] in 'aeiou' else 'a'} {word}"


def _entry_problems(exp: FormatExpectation, name: str, entry: dict) -> list[str]:
    """Field-level mismatches of one merged entry against ``exp``."""
    problems: list[str] = []
    for f in exp.require:
        if f not in entry:
            problems.append(f"'{name}' has no '{f}'")
    for f, want in exp.types.items():
        if f in entry and _json_type_of(entry[f]) != want:
            have = _json_type_of(entry[f])
            problems.append(f"'{name}'.{f} is {_a(have)}, not {_a(want)}")
    for f in exp.forbid:
        if f in entry:
            problems.append(f"'{name}' uses '{f}', which is not accepted")
    for f, allowed in exp.values.items():
        if f in entry and entry[f] not in allowed:
            problems.append(f"'{name}'.{f} is {entry[f]!r}, not one of {', '.join(allowed)}")
    for f, bad in exp.reject.items():
        if f in entry and entry[f] in bad:
            why = f" — {exp.reject_hint}" if exp.reject_hint else ""
            problems.append(f"'{name}'.{f} is {entry[f]!r}{why}")
    return problems


def check_format(
    exp: FormatExpectation, *, artifact: Path | None = None, entries: dict | None = None
) -> list[str]:
    """Every way ``artifact`` / merged ``entries`` miss ``exp`` (empty = compatible)."""
    problems: list[str] = []
    if (
        exp.extension
        and artifact is not None
        and artifact.is_file()
        and artifact.suffix != exp.extension
    ):
        problems.append(f"'{artifact.name}' is not a {exp.extension} file")
    if entries is None:
        return problems
    if exp.events is not None:
        unknown = sorted(k for k in entries if k not in exp.events)
        if unknown:
            problems.append(f"unknown event(s) {', '.join(unknown)}")
    for name, entry in entries.items():
        if isinstance(entry, dict):
            problems.extend(_entry_problems(exp, name, entry))
    return problems


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
    #: Always-loaded instruction/memory file this tool auto-reads (path relative to project
    #: root), e.g. ``.claude/CLAUDE.md`` or ``AGENTS.md``. ``agentry emit triggers`` registers a
    #: managed block of skill triggers here. ``None`` means the tool has no such concept.
    memory_file: str | None = None
    #: component type -> content expectations (phase A: warn on mismatch, install as-is)
    expect: dict[ComponentType, FormatExpectation] = field(default_factory=dict)
    #: component type -> how the tool identifies a component of that type, when it isn't
    #: by path. ``"frontmatter:name"``: by the artifact's frontmatter ``name`` (Claude
    #: agents) — two installs declaring one name collide even at different paths (ADR-003).
    identity: dict[ComponentType, str] = field(default_factory=dict)

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
