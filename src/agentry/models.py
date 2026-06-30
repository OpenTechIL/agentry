"""Typed data models for agentry's config, lockfile and manifest.

These mirror the on-disk shapes:

* :class:`Config`   -> ``.agentry.yml``   (hand-editable intent, committed)
* :class:`Lock`     -> ``.agentry.lock``  (resolved SHAs, generated, committed)
* :class:`Manifest` -> ``.agentry/.manifest.json`` (what is installed, gitignored)
* :class:`SourceDescriptor` -> ``<source-repo>/agentry.yaml`` (optional self-description)

Target identifiers are plain strings so brand-new AI tools can be defined entirely
in config (``target_profiles``). The three built-ins are exposed as constants.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ComponentType(str, Enum):
    """Kinds of AI components agentry can manage."""

    SKILL = "skill"
    AGENT = "agent"
    COMMAND = "command"
    TOOL = "tool"
    HOOK = "hook"
    MCP = "mcp"


class Strategy(str, Enum):
    """How a component is installed into a target tool."""

    LINK = "link"  # symlink a file/dir from the store into the tool's dir
    COPY = (
        "copy"  # copy a file/dir from the store into the tool's dir (self-contained, committable)
    )
    MERGE = "merge"  # inject a JSON fragment into the tool's config file
    GENERATE = "generate"  # run the component's own installer command; track produced files
    LINK_MERGE = "link+merge"  # symlink a script dir AND merge its config, rewriting paths


#: File-based component types install via symlink; the rest merge into config.
LINK_TYPES = frozenset(
    {ComponentType.SKILL, ComponentType.AGENT, ComponentType.COMMAND, ComponentType.TOOL}
)
MERGE_TYPES = frozenset({ComponentType.HOOK, ComponentType.MCP})

#: Whether a component type is a directory (vs a single file) in a source repo.
TYPE_IS_DIR: dict[ComponentType, bool] = {
    ComponentType.SKILL: True,
    ComponentType.AGENT: False,
    ComponentType.COMMAND: False,
    ComponentType.TOOL: True,
    ComponentType.HOOK: False,
    ComponentType.MCP: False,
}

#: File extension for file-based component types (dir types have none).
TYPE_EXT: dict[ComponentType, str] = {
    ComponentType.AGENT: ".md",
    ComponentType.COMMAND: ".md",
    ComponentType.HOOK: ".json",
    ComponentType.MCP: ".json",
}


def strategy_for(ctype: ComponentType) -> Strategy:
    return Strategy.MERGE if ctype in MERGE_TYPES else Strategy.LINK


class SourceType(str, Enum):
    GIT = "git"
    LOCAL = "local"


# -- target identifiers (open set) ---------------------------------------

#: Target id type alias. Any non-empty string is valid; built-ins below ship by default.
TargetName = str


class Target:
    """Namespace of built-in target tool ids (one per driver in ``agentry.drivers``)."""

    CLAUDE = "claude"
    OPENCODE = "opencode"
    CURSOR = "cursor"
    CODEX = "codex"
    GEMINI = "gemini"
    WINDSURF = "windsurf"
    KIMI = "kimi"
    COPILOT = "copilot"
    KIRO = "kiro"
    #: Tool-neutral Agent-Skills layout (.agents/skills); portable across AGENTS.md-aware tools.
    AGENTS = "agents"


BUILTIN_TARGET_NAMES: frozenset[str] = frozenset(
    {
        Target.CLAUDE,
        Target.OPENCODE,
        Target.CURSOR,
        Target.CODEX,
        Target.GEMINI,
        Target.WINDSURF,
        Target.KIMI,
        Target.COPILOT,
        Target.KIRO,
        Target.AGENTS,
    }
)

#: AI-harness slugs that, used as a filename suffix (e.g. ``hooks-cursor.json``), mark a
#: config fragment as belonging to that harness rather than the canonical (Claude) one.
#: Used only for MERGE_TYPES (hooks/mcp) to route per-harness variants to their target.
KNOWN_HARNESS_SLUGS: frozenset[str] = frozenset(
    {"claude", "opencode", "cursor", "codex", "gemini", "kimi", "pi", "windsurf", "copilot", "kiro"}
)


# -- config ---------------------------------------------------------------


class Source(BaseModel):
    """A place components come from: a git repo or a local directory."""

    name: str
    type: SourceType
    url: str | None = None
    path: str | None = None
    ref: str = "main"  # branch / tag / commit for git sources
    # Subdirectory within the source repo where components live (monorepo support).
    # Discovery and artifact resolution happen relative to <source-root>/<subdir>.
    subdir: str | None = None

    @model_validator(mode="after")
    def _check_locator(self) -> Source:
        if self.type is SourceType.GIT and not self.url:
            raise ValueError(f"git source '{self.name}' requires a url")
        if self.type is SourceType.LOCAL and not self.path:
            raise ValueError(f"local source '{self.name}' requires a path")
        if self.subdir and (self.subdir.startswith("/") or ".." in Path(self.subdir).parts):
            raise ValueError(f"source '{self.name}' subdir must be a relative path inside the repo")
        return self


def _check_rel(label: str, p: str) -> None:
    if p.startswith("/") or ".." in Path(p).parts:
        raise ValueError(f"{label} must be a relative path inside the project")


class GeneratorSpec(BaseModel):
    """A component that installs itself by *running its own CLI* (e.g. ``graphify install``).

    Used for tools that ship no symlinkable artifact and instead generate files at install
    time. Running third-party commands is opt-in (``agy sync --allow-run``) — see the
    reconcile engine. ``produces`` is the contract that keeps removal safe: agentry only
    ever deletes the paths listed here.
    """

    # Commands run once before ``command`` (e.g. install the generator tool). Each an argv list.
    setup: list[list[str]] = Field(default_factory=list)
    # The generate command, as an argv list (no shell). Run from the project root.
    command: list[str]
    # Project-relative paths the command creates; agentry tracks + cleans exactly these.
    produces: list[str]

    @model_validator(mode="after")
    def _check(self) -> GeneratorSpec:
        if not self.command:
            raise ValueError("generator 'command' must be a non-empty argv list")
        if any(not c for c in self.setup):
            raise ValueError("each generator 'setup' entry must be a non-empty argv list")
        if not self.produces:
            raise ValueError(
                "generator 'produces' must list at least one path (needed for safe removal)"
            )
        for p in self.produces:
            _check_rel(f"generator produces entry '{p}'", p)
        return self


class Component(BaseModel):
    """A single installable component declared in the config."""

    source: str
    type: ComponentType
    name: str
    enabled: bool = True
    # Optional per-component override of the project-wide target list.
    targets: list[str] | None = None
    # Explicit artifact path within the source, relative to its (subdir-adjusted) root.
    # Bypasses convention/descriptor discovery — use when a repo *is* a skill (``path: "."``)
    # or keeps it at an arbitrary location.
    path: str | None = None
    # If set, the component self-installs by running this spec's command (GENERATE strategy)
    # instead of linking/merging an artifact. Mutually exclusive with ``path``.
    generate: GeneratorSpec | None = None

    @model_validator(mode="after")
    def _check_path(self) -> Component:
        if self.path is not None and self.generate is not None:
            raise ValueError(f"{self.ref}: set either 'path' or 'generate', not both")
        if self.path:
            _check_rel(f"{self.ref}: path", self.path)
        return self

    @property
    def ref(self) -> str:
        """Stable identifier used on the CLI: ``<source>/<type>/<name>``."""
        return f"{self.source}/{self.type.value}/{self.name}"

    def applies_to(self, project_targets: list[str]) -> list[str]:
        return self.targets if self.targets is not None else project_targets


class ProfileRule(BaseModel):
    """One ``target_profiles[tool][type]`` rule: where/how a type installs."""

    strategy: Strategy
    dest: str | None = None  # link / link+merge: destination path template ({name})
    file: str | None = None  # merge / link+merge: target config file
    pointer: str | None = None  # merge / link+merge: top-level JSON key
    # link+merge only: rewrite a command-path prefix in the merged fragment so the
    # symlinked scripts resolve (e.g. "${CLAUDE_PLUGIN_ROOT}/hooks" ->
    # "${CLAUDE_PROJECT_DIR}/.claude/hooks/{name}"). Both optional; {name} expands.
    rewrite_from: str | None = None
    rewrite_to: str | None = None

    @model_validator(mode="after")
    def _check(self) -> ProfileRule:
        if self.strategy in (Strategy.LINK, Strategy.COPY) and not self.dest:
            raise ValueError(f"{self.strategy.value} profile rule requires 'dest'")
        if self.strategy is Strategy.MERGE and not (self.file and self.pointer):
            raise ValueError("merge profile rule requires 'file' and 'pointer'")
        if self.strategy is Strategy.LINK_MERGE and not (self.dest and self.file and self.pointer):
            raise ValueError("link+merge profile rule requires 'dest', 'file' and 'pointer'")
        if (self.rewrite_from is None) != (self.rewrite_to is None):
            raise ValueError("'rewrite_from' and 'rewrite_to' must be set together")
        return self


class Registry(BaseModel):
    """A catalog the project consults: a local file path or an http(s) URL.

    The catalog (``repositories.json``) maps a bare repo name to its source + curated
    components, so ``agy add <name>`` can resolve everything without the user knowing the
    repo URL or path/generate flags. The local-file and hosted-server forms are
    interchangeable (same JSON contract).
    """

    name: str
    location: str  # local path (relative to project root or absolute) or http(s) URL


class Config(BaseModel):
    """The full ``.agentry.yml`` document."""

    version: int = 1
    targets: list[str] = Field(default_factory=lambda: [Target.CLAUDE])
    sources: list[Source] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    # Repository catalogs (``repositories.json``): named source repos resolved whole or
    # narrowed to selected components at ``agy add`` time.
    repositories: list[Registry] = Field(default_factory=list)
    # Override built-in target maps or define entirely new tools (data-driven).
    target_profiles: dict[str, dict[ComponentType, ProfileRule]] = Field(default_factory=dict)

    def source(self, name: str) -> Source | None:
        return next((s for s in self.sources if s.name == name), None)

    def find_component(self, ref: str) -> Component | None:
        return next((c for c in self.components if c.ref == ref), None)

    def active_targets(self) -> set[str]:
        """Every target referenced by the project or any component."""
        active = set(self.targets)
        for c in self.components:
            if c.targets:
                active.update(c.targets)
        return active


# -- source descriptor (optional, lives in the source repo) ---------------


class Dependency(BaseModel):
    """One entry under a provides-entry's ``requires`` — a component this one needs.

    The dependency points at another component (``type`` + ``name``) that lives either
    in an already-configured source (``source``) or in an arbitrary git repo (``url``).
    A ``url`` dependency is resolved transitively into a synthesized source recorded in
    the lockfile only — it never touches ``.agentry.yml``. ``ref`` pins the version.
    """

    type: ComponentType
    name: str
    # Where the dependency lives. With neither set it defaults to the *same source* as the
    # component that declares it (the common case: a skill needing a sibling skill).
    source: str | None = None  # logical name of an existing configured source
    url: str | None = None  # git repo for a transitive (lock-only) source
    ref: str | None = None  # requested version (branch/tag/commit) — git deps only
    subdir: str | None = None  # subdir within the dep's repo (monorepo support)

    @model_validator(mode="after")
    def _check(self) -> Dependency:
        if self.source and self.url:
            raise ValueError("dependency cannot set both 'source' and 'url' — choose one")
        if self.subdir and (self.subdir.startswith("/") or ".." in Path(self.subdir).parts):
            raise ValueError("dependency subdir must be a relative path inside the repo")
        return self


class ProvidesEntry(BaseModel):
    """One entry under ``provides[<type>]`` in a source's ``agentry.yaml``."""

    name: str | None = None
    path: str | None = None  # explicit file/dir, relative to source root
    glob: str | None = None  # glob of matches, relative to source root
    requires: list[Dependency] = Field(default_factory=list)  # components this one needs

    @model_validator(mode="after")
    def _check(self) -> ProvidesEntry:
        if not self.path and not self.glob:
            raise ValueError("provides entry requires 'path' or 'glob'")
        if self.path and not self.name:
            # name may be derived from the path's final component if omitted
            pass
        return self


class SourceDescriptor(BaseModel):
    """``<source-repo>/agentry.yaml`` — a source self-describing its components."""

    version: int = 1
    provides: dict[ComponentType, list[ProvidesEntry]] = Field(default_factory=dict)


# -- repository catalog (served by a file or a future hosted server) ------


class RegistrySource(BaseModel):
    """Where a catalog-listed repo comes from (a git repo or a local dir)."""

    type: SourceType = SourceType.GIT
    url: str | None = None
    path: str | None = None
    ref: str = "main"
    subdir: str | None = None

    @model_validator(mode="after")
    def _check(self) -> RegistrySource:
        if self.type is SourceType.GIT and not self.url:
            raise ValueError("registry source of type 'git' requires a url")
        if self.type is SourceType.LOCAL and not self.path:
            raise ValueError("registry source of type 'local' requires a path")
        return self


class ExposeEntry(BaseModel):
    """One curated component a repository entry surfaces.

    The install *strategy* is derived from ``type`` by the engine (link for file types,
    merge for mcp/hook) — so an MCP rides in with no special handling. ``path``/``generate``
    cover the cases discovery can't infer (a repo whose root *is* the artifact, or a
    self-installing tool).
    """

    type: ComponentType
    name: str
    path: str | None = None  # explicit artifact path within the source (e.g. ".")
    generate: GeneratorSpec | None = None

    @model_validator(mode="after")
    def _check(self) -> ExposeEntry:
        if self.path is not None and self.generate is not None:
            raise ValueError(f"expose '{self.name}': set either 'path' or 'generate', not both")
        return self


class RepositoryEntry(BaseModel):
    """One entry under ``repositories`` — a curated source repo.

    With ``expose`` omitted the whole repo is installed (every component discovery finds);
    with it present, only the listed components are enabled.
    """

    # ``copy`` in JSON; the attribute is renamed to avoid shadowing ``BaseModel.copy``.
    model_config = ConfigDict(populate_by_name=True)

    summary: str | None = None
    source: RegistrySource
    expose: list[ExposeEntry] | None = None
    # Install file/dir components by *copying* (self-contained, committable) instead of the
    # default *symlink*. Opt-in per repo; link stays the built-in default. Resolved into
    # concrete copy profile rules at ``agy add`` time (see registry.build_install_profiles).
    copy_install: bool = Field(default=False, alias="copy")
    # Nest command + agent installs under a ``<repo>/`` subfolder so Claude Code namespaces
    # the slash commands (``.claude/commands/<repo>/adr.md`` -> ``/<repo>:adr``). Skills are
    # left flat (Claude only discovers ``.claude/skills/<name>/SKILL.md``).
    namespaced: bool = True
    # Per-repo target-profile overrides merged into the project's config on ``agy add``.
    # Lets a plugin repo declare how its hooks/mcp install (e.g. a claude hook link+merge
    # rewriting ${CLAUDE_PLUGIN_ROOT}) so the curated install works without manual config.
    target_profiles: dict[str, dict[ComponentType, ProfileRule]] = Field(default_factory=dict)


class RepositoryIndex(BaseModel):
    """A repository catalog — the JSON contract a catalog file or hosted server serves."""

    version: int = 1
    repositories: dict[str, RepositoryEntry] = Field(default_factory=dict)
    # Shareable *driver overlays*, keyed by target name: how some agent installs each
    # component type. Installing one (``agy target add <name>``) merges it into the
    # project's ``target_profiles``, making an otherwise-undefined target resolvable —
    # the community-driver layer. Same shape as ``Config.target_profiles[<target>]``.
    targets: dict[str, dict[ComponentType, ProfileRule]] = Field(default_factory=dict)


# -- lock -----------------------------------------------------------------


class LockEntry(BaseModel):
    """Resolved, pinned state of one source."""

    name: str
    type: SourceType
    url: str | None = None
    path: str | None = None
    ref: str | None = None  # the requested ref (git only)
    resolved: str  # exact commit SHA (git) or content hash (local)
    # True when this source was pulled in transitively (a dependency's ``url``), not
    # declared in .agentry.yml. Such entries live in the lock only.
    synthesized: bool = False


class Lock(BaseModel):
    """The full ``.agentry.lock`` document."""

    version: int = 1
    sources: list[LockEntry] = Field(default_factory=list)

    def entry(self, name: str) -> LockEntry | None:
        return next((e for e in self.sources if e.name == name), None)


# -- manifest -------------------------------------------------------------


class InstalledLink(BaseModel):
    """A symlink agentry created (link strategy)."""

    component: str  # component ref
    target: str
    path: str  # the symlink path, relative to project root


class InstalledCopy(BaseModel):
    """A file/dir agentry copied into a target dir (copy strategy)."""

    component: str  # component ref
    target: str
    path: str  # the copied path, relative to project root


class InstalledMerge(BaseModel):
    """Config keys agentry injected (merge strategy)."""

    component: str  # component ref
    target: str
    file: str  # config file path, relative to project root
    pointer: str  # top-level JSON key the entry lives under (e.g. "mcpServers")
    keys: list[str]  # keys agentry owns under that pointer


class InstalledGenerated(BaseModel):
    """Files a generator command produced (generate strategy), tracked for safe removal."""

    component: str  # component ref
    target: str
    paths: list[str]  # project-relative paths agentry owns and will delete on removal


class InstalledLinkMerge(BaseModel):
    """A symlinked script dir + merged config keys agentry installed (link+merge strategy)."""

    component: str  # component ref
    target: str
    link_path: str  # the symlink path, relative to project root
    file: str  # config file path, relative to project root
    pointer: str  # top-level JSON key the entries live under
    keys: list[str]  # keys agentry owns under that pointer


class Manifest(BaseModel):
    """Record of everything agentry installed on disk (``.agentry/.manifest.json``)."""

    version: int = 1
    links: list[InstalledLink] = Field(default_factory=list)
    copies: list[InstalledCopy] = Field(default_factory=list)
    merges: list[InstalledMerge] = Field(default_factory=list)
    generated: list[InstalledGenerated] = Field(default_factory=list)
    link_merges: list[InstalledLinkMerge] = Field(default_factory=list)
