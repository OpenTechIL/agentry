# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Format warnings for known cross-target mismatches.** agentry still installs components
  as-is. It now warns when the content visibly won't work in a target, and `agentry doctor`
  reports each case under a new `format` category, so `doctor --strict` fails on it. Built-in
  checks cover OpenCode MCP entries in Claude's shape (`args`/`env`, a string `command`, no
  `type`), Gemini commands that aren't `.toml`, Gemini hooks under Claude event names, and Gemini
  MCP entries with `type: "http"` + `url`, which Gemini reads as SSE (use `httpUrl`). Two or more
  warnings collapse into one summary line. `-v` prints them all.
- **`expect:` on `target_profiles` rules** sets the format check for one (tool, type) rule. If
  omitted, the built-in check is inherited, so a strategy-only override such as `copy` keeps the
  warnings. `expect: {}` drops it, and `expect: {…}` replaces it. Fields: `extension`, `events`,
  `require`, `types`, `forbid`, `values`, `reject`, `reject_hint`, `expects`.
- **`agentry keep <ref> [<target>:]<key>` and a per-component `keep:` list.** Use these to own
  a merged `mcp` or `hook` entry yourself, for example an MCP server you've customized. agentry
  stops writing, updating or removing that entry, and `status` shows it as `ok (kept)` instead
  of `drift`. The "edited" and "left in place" warnings, the upgrade warning and `agentry doctor`
  now suggest the exact `agentry keep …` command. It names only the targets where the entry
  drifted (`claude:github`), and uses the bare key only when the entry drifted on every target. A
  bare key also covers targets you add later, and agentry won't create the entry there. Disabling or removing the
  component leaves kept entries in place. To hand an entry back, remove it from `keep:` and
  delete your entry. If a hook pack's events depend on each other, keep or delete them together;
  agentry handles each entry independently.

### Changed
- **Behavior change: agentry no longer overwrites config entries it didn't add.** Previously,
  when an `mcp` or `hook` component merged a key that already existed in the target file
  (`.mcp.json`, `.claude/settings.json`, `.vscode/mcp.json`, …), agentry overwrote your entry,
  and removing the component later deleted it. Now sync leaves your entry unchanged and warns.
  On first install, the component is not merged into that file at all, so nothing is
  half-installed. Once a component is installed, a conflict holds back only that entry, and the
  component's other entries keep updating. `agentry status` shows the reason and
  `agentry doctor --strict` fails. If your entry is identical to the one the component ships,
  agentry uses yours and never removes it.
- **Hand edits to entries agentry installed are kept.** If you edit an entry agentry merged,
  sync keeps your version instead of reverting it, and reports it as `drift` in `status` and
  `doctor`. To get agentry's version back, delete the entry and run `agentry sync`.
- A config section that agentry merges into but that isn't an object (e.g. `"mcpServers": []`)
  is no longer replaced. The components that target it are skipped with a warning.
- `agentry status` now prints a reason under the table for each row that isn't `ok`, and
  `agentry doctor` shows that reason instead of always suggesting `agentry sync`.
- The install manifest moves to version 2 and is migrated automatically on the next sync. An
  older agentry can still read it, but downgrading is not supported.
- **Upgrading: run `agentry sync` once before `agentry update`.** On that first sync, agentry
  checks the config entries it installed in earlier versions. It keeps managing an entry only if
  the entry still matches what the component ships. It leaves any other entry untouched and
  reports it as `drift`, and it won't remove that entry later. If you run `update` first, entries
  from the old upstream version no longer match and can't be verified. Delete stale entries by
  hand.

- **CLI output change: repeated warnings are summarized.** When `sync`, `install`, `update` or
  `add` would print the same kind of warning two or more times, they now print one summary line
  instead. This affects:
  - components skipped on targets that don't support their type;
  - merged entries that differ from their source and were kept.

  A warning that occurs only once still prints in full. Every other warning still prints
  individually: refused merges, unparseable files, the trust gate, and unset `${VAR}`s. **To
  restore the previous full output, pass `--verbose` / `-v`.** If a script parses agentry's
  warnings, add `-v`.
- Warning lines from `sync`, `add` and `update` are no longer broken at the terminal width when
  output goes to a log or pipe. Each warning is one line, so it's easy to grep. In a terminal,
  long lines still wrap on screen.

### Fixed
- **Per-harness variants now replace the canonical fragment on their target, as documented.**
  This applies when an `mcp` or `hook` component has an enabled `<base>-<target>.json` variant
  from the same source, such as `mcp/github-opencode.json` next to `mcp/github.json`.
  Previously both installed into that target, so the variant collided with the canonical entry
  or the hook ran twice. Now only the variant installs there, and the other targets keep the
  canonical file. **Compatibility:** install results change if you use variants. The next sync
  removes the canonical entry agentry had added on that target and installs the variant, in a
  single sync. Entries you added by hand are left untouched. `agentry why <ref>` shows the
  routing, for example `opencode ← github-opencode.json (variant; replaces github.json)`.
- A component's `transform:` was dropped from `.agentry.yml` when the component was added through
  the Python config API (`ConfigStore.add_component`). A `transform:` you write by hand in
  `.agentry.yml` was not affected, and is kept when agentry commands rewrite the file.

## [0.1.5] — 2026-09-09

### Added
- `agentry target use <name>` — activate a target (built-in, or already defined via
  `target add` / hand-written `target_profiles`) for the current project: adds it to
  `targets:` in `.agentry.yml` and syncs. Idempotent — a no-op if already active.
- `agentry target drivers` — list agentry's built-in target drivers (`claude`, `opencode`,
  `cursor`, `codex`, `gemini`, `windsurf`, `kimi`, `copilot`, `kiro`, `agents`).
- `agentry init -t <name>` is now safe to re-run on an already-initialized project: it adds
  the given target(s) to the existing `.agentry.yml` and syncs, instead of erroring with
  "Already initialized". **Behavior change:** running bare `agentry init` (no `-t`) on an
  already-initialized project now prints an informational message and exits **0** instead
  of exiting 1.

## [0.1.4] — 2026-09-03

### Added
- **`agentry` is now the CLI's canonical command name**, alongside a new short alias
  `agyx`. `agy` still works, but it is *also* the command for Google's Antigravity CLI —
  when both are installed, PATH order silently decides which one runs. agentry now prints a
  one-line notice on stderr if it is invoked as `agy` while PATH resolves `agy` elsewhere
  (silence it with `AGENTRY_NO_COLLISION_WARN`), and `agentry doctor` reports the conflict
  as a check. Every install channel ships all three names.
- `--version` / `-V` on the root command. The bug-report template already asked for
  `--version`, which errored — version was only available as a subcommand.
- `agentry init` now registers agentry's curated catalog by default (name `agentry`, pointing at
  `https://raw.githubusercontent.com/OpenTechIL/agentry/refs/heads/main/registry/repositories.json`),
  so `agentry add arckit` and friends resolve on a fresh install with no `agentry catalog add`
  step. Opt out with `agentry init --no-default-catalog`; drop it later with
  `agentry catalog remove agentry`.
- New docs: `docs/config-reference.md` (every `.agentry.yml` key), `docs/authoring.md`
  (component repos, descriptors, catalog entries) and `docs/troubleshooting.md`.

### Changed
- Release assets are named `agentry-<version>-<target>`. A byte-identical copy is published
  under the old `agy-` name for this release only, because `install.sh`/`install.ps1`
  shipped before this version fetch that name when self-updating; both installers now try
  the current name first and fall back.
- Homebrew installs `Formula/agentry.rb`; `brew install OpenTechIL/tap/agy` keeps working
  as a tap alias.
- User-facing hints interpolate the command name you actually invoked, so an `agyx` user is
  no longer told to run `agy`. Text agentry *writes to disk* keeps the canonical name, so
  generated files stay byte-stable however the tool was invoked.
- `.agentry.yml` writes no longer fold long scalars onto a continuation line (ruamel width),
  keeping catalog URLs on one line.
- Tooling: mypy is now configured and runs in CI and pre-commit (the codebase was
  pervasively annotated but had no checker); 16 missing signatures are annotated, starting
  with `Driver.strategy`, whose `| None` return callers already branched on. ruff gains
  `S` (bandit), `BLE`, `RUF100` and `C901` — the existing `# noqa: S310/S603/BLE001`
  comments suppressed rules that were never selected, so they claimed a check that wasn't
  happening. Coverage is gated at 85% (previously measured and ignored). CI adds a macOS
  and a Windows job, since symlinks are the default install strategy and only Ubuntu was
  tested. ruff is pinned to one version in both CI and pre-commit, which previously
  disagreed despite a comment claiming otherwise.
- Internal: `compute_desired` returned a bare 6-tuple from 153 lines; it now returns a
  named `DesiredPlan` and the strategy dispatch is its own function. Catalog loads are
  memoized per process — `find_repo`/`list_repos`/`find_target`/`list_targets` each looped
  over every catalog, so one command re-fetched the same URL three or four times. The
  repo-basename derivation existed in four copies and is now one (`naming.py`). Added
  direct tests for the installer primitives, `envscan` and the `Driver` abstraction, which
  were only ever exercised transitively.
- The module map is maintained in `docs/architecture.md` §8 only. `AGENTS.md` and
  `CONTRIBUTING.md` link to it; their own copies had drifted and are gone.

### Fixed
- **Path confinement.** `target_profiles` / catalog-overlay `dest` and `file` values were
  never validated, unlike `Component.path`. Since `Path(root) / "/abs"` discards `root`, a
  catalog could write outside the project. They are now validated at parse time and
  re-checked at the filesystem boundary by every installer. The relative-path check is also
  cross-platform now (it previously missed `C:\…` and UNC paths).
- **`repo_basename` mishandled Windows paths.** It split only on `/`, so a local source
  path like `C:\Users\me\hooksrc` came back whole — and a `link+merge` `dest` template of
  `.claude/hooks/agentry/{repo}@{ref}/{name}` expanded to a destination outside the project.
  The new path-confinement guard turned that into a loud refusal instead of a mangled
  install path, and the new Windows CI job surfaced it. Also fixed the same class in
  transitive dependency-source synthesis.
- **Explicit UTF-8 for every file read and write.** `Path.read_text()` / `write_text()`
  without an `encoding` use the platform locale, so content containing non-ASCII (the em
  dashes in `CHANGELOG.md` and in `emit triggers` output) round-tripped incorrectly on
  Windows. All ~190 call sites in the suite, plus `scripts/bump.py` and one in `cli.py`,
  now pass `encoding="utf-8"`.
- **`git clone` URL allowlist.** A source URL from a catalog went straight to `git clone`,
  and git's `ext::` transport executes a shell command at clone time.
- **`agentry target add` asks before applying.** It previously fetched a remote driver
  overlay, merged it into `.agentry.yml` and synced with no confirmation. It now prints the
  destinations the overlay will write to and prompts (`--yes` for CI).
- **Timeouts.** The catalog fetch and all three subprocess call sites (`git`, generator
  commands, the transform agent CLI) were unbounded, so an unreachable host hung the CLI
  with stdout captured and nothing on screen. Catalog responses are also size-capped, and
  the scheme is re-checked after redirects.
- Documentation accuracy: the contributor quickstart cloned a non-existent org
  (`opentech/agentry`); CONTRIBUTING described a `release.yml` PyPI workflow that no longer
  exists; `AGENTS.md` said seven built-in agents (there are nine) and omitted `tomlkit` from
  the runtime deps; README claimed Windows "falls back to copies automatically" for symlinks
  (it does not — see Troubleshooting), called Cursor "rules-only" (it supports MCP),
  documented `source add NAME URL` without `--local`, promised a pipx route it never showed,
  said "three ways" above four items, and omitted eight real commands including
  `agentry remove`, which it cites twice as a headline guarantee. Dead cross-document
  anchors are fixed, and the two em-dash headings that slugified differently on GitHub and
  MkDocs no longer do.

## [0.1.3] — 2026-07-14

### Fixed
- Dead-placeholder env scan (`envscan.unset_env_refs`) no longer flags `${…PLUGIN_ROOT}`
  variables as unset. They are host-injected and rewritten by the `link+merge` path, so the
  generic "set it before your agent runs" advice was a false positive when a link+merge hook
  is resolved by discovery to a file (raw `${CLAUDE_PLUGIN_ROOT}` seen before rewrite). The
  plain-`merge` path still warns about them via `plugin_root_refs`.
- `superpowers` catalog entry now carries a `claude` hook `target_profiles` rule
  (`link+merge` with `${CLAUDE_PLUGIN_ROOT}/hooks` → `${CLAUDE_PROJECT_DIR}/.claude/hooks/…`
  rewrite). Without it, installing `superpowers` merged a plugin-only `${CLAUDE_PLUGIN_ROOT}`
  command into `.claude/settings.json`, which Claude Code rejects at startup with a
  `SessionStart` hook error.

### Added
- `agy emit triggers` — registers a **skill-trigger** block (each installed skill's name →
  its `SKILL.md` `description`, i.e. *when to auto-invoke it*) into every active target's
  memory file (`.claude/CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, …). Teaches harnesses that don't
  auto-load skills when to reach for an agentry-managed skill. Writes only a marker-delimited
  block (hand-authored content preserved), is idempotent, and supports `--check` for CI and
  `-o FILE` to target a single file. Backed by a new `TargetSpec.memory_file` (declared per
  built-in driver) — the repo's first markdown merge, alongside the existing JSON config merges.
- Defensive guard in the plain-`merge` hook path (`reconcile.py`): when a hook fragment
  still references a `${…PLUGIN_ROOT}` variable after filtering, `agy sync` now emits a
  warning pointing at the `link+merge` profile fix instead of silently installing a hook
  the harness rejects. Backed by a new `link_merge.plugin_root_refs()` helper.
- Standalone `agy` binaries for Windows, macOS, and Linux, built with PyInstaller
  and attached to each GitHub Release (`release-binaries.yml`), plus `install.sh` /
  `install.ps1` one-line installers that download and checksum-verify the binary.
- `scripts/bump.py X.Y.Z` to bump the version across `pyproject.toml`,
  `src/agentry/__init__.py`, and `CHANGELOG.md`, then commit and tag in one step.
- `agy catalog add-repo <git-url> [name]` to author entries in a curated catalog
  (`registry/repositories.json` by default, `--file` to override). Writes a minimal
  `summary` + `source` entry; `--discover` clones the repo and pre-fills `expose` from
  discovered components. A `…/tree/<ref>/<subdir>` URL infers `ref`/`subdir`, and the name
  defaults to the repo basename. New `registry.parse_repo_url` and `registry.add_entry`
  helpers back the command.
- `agy add <repo>@name[,name]` to install only selected components from a catalog repo,
  and `--type/-T` (repeatable: skill/agent/command/hook/mcp) to filter a catalog install by
  component type. A bare `agy add <repo>` opens an interactive picker in a TTY and installs
  everything otherwise.
- Path templating for link+merge profile destinations: `{name}`, `{source}`, `{repo}`
  (repo basename), and `{ref}` (git ref, `/` flattened to `-`). Lets a profile namespace
  linked dirs per repo+ref, e.g. `.claude/hooks/agentry/{repo}@{ref}/{name}`, instead of
  colliding on `{name}`.
- Contributor onboarding in the README: a Contributing section (dev setup, PR/CI
  expectations, the idempotency/safety rule) and a Code of Conduct link.
- `ponytail` and `caveman` catalog entries in `registry/repositories.json`
  (minimal-code guidance and output-compression skills).

### Changed
- Renamed the catalog command surface for clarity: the consumer group `agy repo`
  (`add`/`remove`/`list`) is now `agy catalog`, and catalog authoring is the
  `agy catalog add-repo` subcommand — sitting beside `agy catalog add` (register a catalog to
  consume) vs `agy catalog add-repo` (author an entry). Hard rename — the old `repo`/`registry`
  command names are gone. The internal `repositories:` config key, `Registry` model, and
  `registry.py` module keep their names.
- Consolidated the skill-registry and repository-catalog systems into a single catalog:
  `repositories.json` / `repositories:` / `agy catalog` is now the only name-based resolution
  path. `agy add <name>` resolves a repo from the configured catalogs.
- `agy search` now searches catalog repositories (name/summary) instead of registry skills.
- Migrated the starter `ui-ux-pro-max` and `graphify` entries into
  `registry/repositories.json` as `expose` entries (carrying their `path`/`generate`).
- Reworked `link_merge.rewrite_fragment` to take an already-expanded `rewrite_to` prefix;
  the caller (`reconcile`) now owns placeholder substitution via `_link_merge_vars`/`_expand`.
- Docs CI now builds on pull requests too (strict, no deploy) so broken links/anchors are
  caught before merge; deploy to GitHub Pages is gated to pushes on `main`. The build uses
  `uv run --extra docs` (pinned `mkdocs-material<10` in `pyproject.toml`) instead of an
  unpinned inline dependency, and `mkdocs.yml` promotes `links.not_found`/`links.anchors`
  to warnings so `--strict` fails on dead internal links and missing anchors.
- Python install instructions in the README and the `uv tool install agentry` fallback
  hints in `install.sh`/`install.ps1` now use the git source
  (`git+https://github.com/OpenTechIL/agentry`) instead of the unpublishable PyPI name.

### Fixed
- Broken CI badge and links in the README: the badge image, `uvx --from git+…`
  install command, "File an issue" link, and `git clone` URL all pointed at the
  wrong GitHub org (`opentech/agentry`), so the badge and links 404'd. Corrected
  all four to the real repo path `OpenTechIL/agentry`.
- Docs `mkdocs build --strict` failure: `commands.md` linked to `../README.md` (outside
  `docs_dir`, unresolvable) and a heading anchor with a stray double hyphen. Pointed the
  README link at its GitHub URL, corrected the anchor, and added `commands.md` to the nav.
- Per-harness hook/MCP fragments are now routed only to their matching target. A repo
  shipping tool-specific variants side by side (e.g. superpowers' `hooks/hooks.json`,
  `hooks/hooks-cursor.json`, `hooks/hooks-codex.json`) no longer merges the Cursor/Codex
  variants into Claude's `.claude/settings.json` — previously Cursor's camelCase
  `sessionStart` (which Claude Code rejects) and Codex's colliding `SessionStart` leaked
  in. Discovery tags a `<base>-<harness>` fragment with its harness (`discovery.harness_suffix`,
  `KNOWN_HARNESS_SLUGS`); reconcile skips variants whose harness isn't the target. As
  defense-in-depth, hook events outside `CLAUDE_HOOK_EVENTS` are dropped from Claude's
  settings with a warning. The fix self-heals affected projects on the next `agy sync`,
  and `agy add` no longer records foreign-harness variants for inactive targets.
- A changed link+merge `dest` template now removes the stale symlink at the old path during
  reconcile, instead of leaving an orphaned link behind.
- `registry.add_entry` writes catalog JSON with `ensure_ascii=False`, keeping non-ASCII
  characters (e.g. an em-dash in a summary) literal rather than escaping them to `\uXXXX`.

### Removed
- PyPI distribution: deleted the `release.yml` publish workflow and the PyPI version
  badge. The name `agentry` is owned by an unrelated PyPI project, so publishing was
  impossible; standalone binaries (`release-binaries.yml`) + `install.sh`/`install.ps1`
  are now the sole distribution channel.
- The skill registry: `registry/skills.json`, the `registries:` config key, the
  `agy registry` command group, the `RegistrySkill`/`RegistryIndex` models, the
  `load_index`/`find`/`list_skills` resolver functions, and the
  `add_registry`/`remove_registry` config mutators.
