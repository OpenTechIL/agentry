# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] — 2026-06-25

### Added
- `agy publish <git-url> [name]` to author entries in a curated catalog
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

### Changed
- Renamed the catalog command surface for clarity: the consumer group `agy repo`
  (`add`/`remove`/`list`) is now `agy catalog`, and catalog authoring moved from the
  `agy registry add` subcommand to the flat top-level `agy publish` command. Hard rename —
  the old `repo`/`registry` command names are gone. The internal `repositories:` config
  key, `Registry` model, and `registry.py` module keep their names.
- Consolidated the skill-registry and repository-catalog systems into a single catalog:
  `repositories.json` / `repositories:` / `agy catalog` is now the only name-based resolution
  path. `agy add <name>` resolves a repo from the configured catalogs.
- `agy search` now searches catalog repositories (name/summary) instead of registry skills.
- Migrated the starter `ui-ux-pro-max` and `graphify` entries into
  `registry/repositories.json` as `expose` entries (carrying their `path`/`generate`).
- Reworked `link_merge.rewrite_fragment` to take an already-expanded `rewrite_to` prefix;
  the caller (`reconcile`) now owns placeholder substitution via `_link_merge_vars`/`_expand`.

### Fixed
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
- The skill registry: `registry/skills.json`, the `registries:` config key, the
  `agy registry` command group, the `RegistrySkill`/`RegistryIndex` models, the
  `load_index`/`find`/`list_skills` resolver functions, and the
  `add_registry`/`remove_registry` config mutators.
