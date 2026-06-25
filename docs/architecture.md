# agentry — Architecture

> The canonical design reference. If you're changing behavior, update this doc.

## 1. Problem: AI integration chaos

The AI ecosystem is expanding without standardization. A project that uses AI coding
tools accumulates **skills, agents, commands, tools, hooks, and MCP servers**, each of
which has to be dropped into a tool-specific location by hand:

- Claude Code reads `.claude/skills/`, `.claude/agents/`, `.claude/commands/`, `.claude/settings.json`, `.mcp.json`
- OpenCode reads `.opencode/…` and `opencode.json`
- Cursor reads `.cursor/rules/` and `.cursor/mcp.json`

Copy-pasting these by hand produces the classic failures software solved long ago:
no versioning, no single source of truth, duplicated effort across projects, drift
between machines, and no safe way to uninstall. This is **dependency hell** for AI.

## 2. Solution: dependency management for AI

`agentry` treats AI components as packages, modeled on `pip` / `yarn` / `uv`:

| File | Role | Committed? |
|---|---|---|
| `.agentry.yml` | **Intent** — declared sources + components (hand-editable) | ✅ yes |
| `.agentry.lock` | **Resolved truth** — exact commit SHAs / content hashes | ✅ yes |
| `.agentry/` | **Store** — downloaded git clones / local symlinks | ❌ gitignored |
| `.agentry/.manifest.json` | **Reality** — what is actually installed on disk | ❌ gitignored |

The three-way relationship is the heart of the design:

```
.agentry.yml  ──declares──▶  desired state
.agentry.lock ──pins──────▶  exact versions   ┐
.manifest     ──records───▶  installed state  ┘──▶ reconcile() makes disk match
```

## 3. Component model

```
ComponentType: skill | agent | command | tool | hook | mcp
Strategy:      link (file-based) | merge (config-based) | generate (self-installing)
```

| Type | Strategy | Why |
|---|---|---|
| skill, agent, command, tool | **link** | They are files/dirs a tool reads from a directory |
| hook, mcp | **merge** | They are entries inside a tool's JSON config, not standalone files |
| any (opt-in) | **generate** | The component has no symlinkable artifact and installs itself by running its own CLI |

**Generate strategy.** A component may carry a `generate` spec (`setup`/`command`/`produces`)
instead of an artifact — for tools like graphify that ship no skill file and generate one at
install time. Running third-party commands is **opt-in** (`agy sync --allow-run`) and the
commands are printed before execution; `produces` lists the project-relative paths agentry
tracks so removal deletes exactly those and nothing else. See `installers/generate.py`.

## 4. Source-repo layout — convention or descriptor

A source (git repo or local dir) provides components in one of two ways. `discovery.py`
picks the descriptor when present, else falls back to the convention scan.

**Convention** — mirror the standard agent layout:

```
skills/<name>/        directory (e.g. contains SKILL.md)        → link
agents/<name>.md      file                                      → link
commands/<name>.md    file                                      → link
tools/<name>/         directory                                 → link
hooks/<name>.json     JSON object of named entries              → merge
mcp/<name>.json       JSON object of named entries              → merge
```

**Descriptor** — an optional `agentry.yaml` (or `.yml`) at the source root lets a repo
**self-describe** an arbitrary layout. Each `provides` entry is either an explicit
`{ name, path }` or a `{ glob }` (name derived from each match — file stem, or dir name):

```yaml
# <source-repo>/agentry.yaml
version: 1
provides:
  skill:   [ { name: code-reviewer, path: packages/code-reviewer } ]
  agent:   [ { glob: "ai/agents/*.md" } ]
  mcp:     [ { glob: "servers/*.json" } ]
```

The component *type* still dictates shape (dir vs file + extension); the descriptor only
says *where*. Absent ⇒ convention scan (full back-compat).

**Consumer-side overrides (third way).** When a source follows neither layout — a common
case for third-party skills whose repo *is* the skill — the consumer's `.agentry.yml`
component can resolve it directly, bypassing discovery:

- `path:` on a component points at an explicit artifact within the source (`path: "."` ⇒
  the source root is the skill). Handled in `reconcile.compute_desired`.
- `generate:` on a component installs via the generate strategy instead of an artifact.

**Catalogs (`registry.py`).** A `repositories:` list in `.agentry.yml` points at JSON
catalogs (a local file or an http(s) URL) that map a bare repo name to its source + optional
curated components. `agy add <repo>` consults them in order and synthesizes the same Sources +
Components a user would hand-write, installing all of them, a `@name`-selected subset, or a
`--type`-filtered subset — so catalogs add resolution only, no new install mechanics. The
catalog is the JSON contract a hosted "artifactory" server would serve, so file and server are
interchangeable. URL catalogs are cached under `.agentry/repositories/`.

**Dependencies (`requires`).** A descriptor entry may declare components it needs. Each
`requires` item points at another component by `type` + `name`, living in one of three
places — and is version-pinnable via `ref`:

```yaml
provides:
  skill:
    - name: code-reviewer
      path: skills/code-reviewer
      requires:
        - { type: skill, name: shared-style }                       # same source (this repo)
        - { type: tool, name: ripgrep, source: utils }              # another configured source
        - { type: skill, name: linter, url: "https://github.com/acme/linters.git", ref: v2 }
```

A `url` dependency is pulled in **transitively**: agentry synthesizes a source for it,
records it in `.agentry.lock` (marked `synthesized: true`), and installs it — but never
writes it into `.agentry.yml`. Intent stays minimal; the lock captures the full closure.
Only descriptor sources can declare dependencies (the convention scan carries none).

**Merge fragment contract.** A `hooks/*.json` or `mcp/*.json` file is a JSON **object of
named entries**. Each top-level key is merged under the target's pointer, and agentry
records exactly those keys so removal never disturbs hand-added entries. Example
`mcp/github.json`:

```json
{ "github": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"] } }
```

A fragment may also arrive **wrapped** under the section name it targets — the shape
real-world plugin files ship. A Claude Code `hooks.json` is
`{ "description": ..., "hooks": { "Stop": [...] } }` and an `.mcp.json` is
`{ "mcpServers": { ... } }`. `merge.select_entries` unwraps such a fragment using the
destination's `wrapper_keys` (the `pointer` plus any `aliases` — e.g. OpenCode's `mcp`
config also accepts the Claude-style `mcpServers` wrapper), so the real named entries
are merged and sibling metadata like `description` is dropped. An already-flat fragment
is used unchanged, so both shapes work.

## 5. Target capability map (`targets.py`) — built-in + config

Each target declares, per component type, a **link destination** (path template) or a
**merge destination** (config file + JSON pointer). A type absent from both is
unsupported → skipped with a warning.

The table below is the **built-in default**. `resolve_targets(config)` deep-merges the
project's `target_profiles` over it — overriding a path on an existing tool, or defining
a brand-new tool entirely in config (target ids are open strings, not a closed enum):

```yaml
# .agentry.yml
targets: [claude, mycli]              # active tools — may include custom names
target_profiles:
  mycli:                              # a new tool, no code required
    skill: { strategy: link,  dest: ".mycli/skills/{name}" }   # quote: {name} is literal
    mcp:   { strategy: merge, file: ".mycli/config.json", pointer: "mcpServers" }
  claude:
    tool:  { strategy: link,  dest: ".claude/plugins/tools/{name}" }   # override one path
```

> YAML note: a `dest` containing `{name}` must be **quoted**, or YAML reads `{…}` as a flow mapping.

An active target with neither a built-in nor a profile is reported via `unresolved_targets`.

| Type | Claude Code | OpenCode | Cursor |
|---|---|---|---|
| skill | `.claude/skills/{name}` | `.opencode/skills/{name}` | — |
| agent | `.claude/agents/{name}.md` | `.opencode/agents/{name}.md` | `.cursor/rules/{name}.mdc` |
| command | `.claude/commands/{name}.md` | `.opencode/commands/{name}.md` | `.cursor/rules/{name}.mdc` |
| tool | `.claude/tools/{name}` | `.opencode/tools/{name}` | — |
| hook | merge `.claude/settings.json` → `hooks` | — | — |
| mcp | merge `.mcp.json` → `mcpServers` | merge `opencode.json` → `mcp` | merge `.cursor/mcp.json` → `mcpServers` |

## 6. The reconcile flow (`agy sync`)

```
1. Resolve sources + dependency closure   deps.resolve_graph(config, lock, update=…)
   ├─ resolve every config source         resolver.resolve(source, pinned=lock_sha or None)
   │    ├─ git:   clone once → fetch → checkout --detach <sha>  → resolved = SHA
   │    └─ local: symlink store/<name> → abspath(path)          → resolved = sha256(tree)
   ├─ walk requires from the enabled roots (BFS), recursing into each dep's own descriptor
   │    ├─ url dep → synthesize a lock-only source, download, recurse
   │    ├─ cycles broken by a visited set on the component ref
   │    └─ version conflict (same repo/source, two refs) → abort with a clear error
   └─ emit an augmented (sources + components) graph; write .agentry.lock.

2. Compute desired state          reconcile.compute_desired(augmented_config)
   for each ENABLED component × each applicable target:
     LINK     → DesiredLink(dest path, store artifact)   [path: override skips discovery]
     MERGE    → DesiredMerge(config file, pointer, fragment keys)
     GENERATE → DesiredGenerate(component, generate spec)

3. Diff against the manifest and apply
   links:     remove manifest links not desired (safe-remove) → create/refresh desired
   merges:    remove manifest merges not desired (strip keys) → inject desired keys
   generated: remove orphans (delete produced paths) → run (only with --allow-run), record produced paths

4. Persist manifest; ensure .agentry/ is in .gitignore.
```

`sync` is **idempotent**: a second run with the same inputs is a no-op. With
`update=True` it ignores the locked SHA, re-resolves each ref to its tip, and rewrites
the lock — the only operation that advances versions.

## 7. Safety model

agentry never destroys anything it didn't create. Two invariants enforce this:

- **Links** — `link.remove_link` / overwrite only act on a path that is a symlink whose
  *lexical* target resolves into `.agentry/`. A user-authored file or an unrelated
  symlink at the same path is left alone (and overwrite raises rather than clobbers).
  The check is lexical (`abspath`, not `resolve`) so a local source — itself a symlink
  in the store — is still recognized as managed.
- **Merges** — `merge.remove_merge` only deletes the specific keys recorded in the
  manifest. Hand-added entries under the same pointer survive.

## 8. Module map

```
cli.py          Typer app — command wiring + Rich output
models.py       pydantic: Config, Source, Component, Lock, Manifest, enums
config.py       .agentry.yml round-trip (ruamel, comment-preserving) + mutators
lockfile.py     .agentry.lock read/write
targets.py      per-tool capability map (TargetSpec)
discovery.py    scan a source for available components + their `requires` (LAYOUT)
resolver.py     download/checkout into the store; resolve refs → SHA/hash
deps.py         transitive dependency closure (recursive, version-aware) → augmented graph
registry.py     resolve a bare repo name via external catalogs (file/URL) → Sources + Components
manifest.py     .agentry/.manifest.json read/write
installers/
  link.py       symlink create/remove/state (lexical, store-scoped)
  merge.py      JSON inject/remove/state (key-scoped, reversible)
  generate.py   run a component's own installer (gated); track produced files for safe removal
reconcile.py    sync engine + status (drift report)
gitignore.py    ensure .agentry/ is ignored
```

## 9. Extension points

- **New target tool** — *no code needed*: define it under `target_profiles` in `.agentry.yml`.
  To ship it as a built-in, add a `TargetSpec` to `targets.BUILTIN_TARGETS`.
- **New component type** — add to `ComponentType`, `LINK_TYPES`/`MERGE_TYPES`, `TYPE_IS_DIR`/
  `TYPE_EXT`, and a destination in each relevant `TargetSpec`.
- **New source layout** — *no code needed*: ship an `agentry.yaml` descriptor in the source repo.
- **New source kind** — add a `SourceType` and a branch in `resolver.resolve`.

## 10. Deferred (future phases)

- **Hosted catalog server** — the catalog format and name-based `agy add`/`agy search`
  ship today against file/URL catalogs (`registry.py`); a hosted catalog server + publish flow
  (serving the same JSON contract) is the remaining piece.
- **Compatibility metadata** — components declare supported model/tool versions; sync warns on mismatch.
- **Hook array-merge** — richer merging for event-keyed hook arrays beyond the named-key contract.
- **Copy fallback** — copy instead of symlink for filesystems without symlink support (Windows).
- **TUI** — a Textual front-end over this same core (browse/toggle/sync).
