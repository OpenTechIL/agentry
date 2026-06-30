# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] — 2026-06-25

### Fixed
- `superpowers` catalog entry now carries a `claude` hook `target_profiles` rule
  (`link+merge` with `${CLAUDE_PLUGIN_ROOT}/hooks` → `${CLAUDE_PROJECT_DIR}/.claude/hooks/…`
  rewrite). Without it, installing `superpowers` merged a plugin-only `${CLAUDE_PLUGIN_ROOT}`
  command into `.claude/settings.json`, which Claude Code rejects at startup with a
  `SessionStart` hook error.

### Added
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
