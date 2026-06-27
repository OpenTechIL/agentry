# Knowledge Base

Project-specific pitfalls, patterns, constraints, and discoveries captured during development sessions.

---

## 2026-06-27 — MkDocs `--strict` CI failures & guardrails

**Context:** The GitHub Pages docs build ([.github/workflows/docs.yml](https://github.com/opentech/agentry/blob/main/.github/workflows/docs.yml)) failed because [commands.md](commands.md) carried a link to `../README.md` (outside `docs_dir`) and a broken heading anchor. Fixed the links, then hardened the pipeline against recurrence.

**Findings:**

- **`mkdocs build --strict` aborts on WARNING, but broken anchors are only INFO by default.** A dead internal link (`links.not_found`) is a WARNING → fatal under `--strict`; a missing heading anchor (`links.anchors`) is INFO → silently shipped. To make anchor typos fail too, add a `validation:` block to `mkdocs.yml` promoting both to `warn`.
- **Links outside `docs_dir` can't be relative.** `../README.md` lives at the repo root, so MkDocs can't resolve it as a page. Use the absolute GitHub blob URL instead (matches the existing Contributing/Changelog nav pattern).
- **Default `toc` slugify collapses runs of spaces/dashes to a single hyphen.** Heading `## 4. Source-repo layout — convention or descriptor` (em-dash) → anchor `#4-source-repo-layout-convention-or-descriptor`. A hand-written double `--` where the em-dash was is wrong.
- **The docs workflow only ran on push to main, so link breakage was caught *after* merge.** Added a `pull_request` trigger (strict build, no deploy via `if: github.event_name == 'push'`) so reviews catch it. Concurrency keyed per-ref (`pages-${{ github.ref }}`) with `cancel-in-progress` only on PRs keeps main deploys uninterrupted while cancelling redundant PR runs.
- **Pin `mkdocs-material<10`.** Material 2.0 is announced as a hard break (plugin system removed, no migration path); an unpinned `--with mkdocs-material` would break the build on its release. Moved the build to `uv run --extra docs` so the pinned version in `pyproject.toml`'s `docs` extra is the single source of truth.

---

## 2026-06-26 — Command rename: `publish` → `catalog add-repo`

**Context:** README reorg for the OSS release surfaced that `agy publish` misrepresented its
action — it publishes nothing; it adds/edits a repo entry in a local catalog file (its success
message is literally `Added {name} → {file}`). Renamed it to **`agy catalog add-repo`**, nested
under the `catalog` group. Hard rename, no alias (consistent with the earlier `repo`/`registry`
rename below). `agy publish` never shipped in a release, so the CHANGELOG Unreleased entry was
updated in place rather than logging a rename.

**Findings:**

- **The `catalog` group now pairs two near-identical verbs intentionally:** `catalog add`
  (register a catalog to *consume*) vs `catalog add-repo` (*author* an entry into a catalog
  file). Both docstrings disambiguate to keep `--help` clear.
- **The deep catalog detail moved out of the README** into [architecture.md](architecture.md)
  (catalog repo schema incl. `copy`/`namespaced` flags + JSON example, per-harness fragment
  routing) and the full command table into [commands.md](commands.md). The README links down.

---

## 2026-06-25 — Command rename: `repo`/`registry` → `catalog`/`publish`

**Context:** Consolidated the confusing command trio for OSS release. The consumer side
(`agy repo add`/`remove`/`list` — register/browse a catalog) became the **`agy catalog`**
group. The producer side (`agy registry add` — author an entry in `registry/repositories.json`)
became the flat top-level **`agy publish`** command. Hard rename — the old `repo` and
`registry` command names are gone (no aliases). `agy source` is unchanged.

**Findings:**

- **Only the CLI surface changed.** The `.agentry.yml` `repositories:` key, the `Registry`
  model, and the `registry.py` module keep their names — renaming the config key would break
  existing files, and the module/model are user-invisible. So "catalog" is the user-facing
  noun while "registry"/"repositories" persist internally; this is intentional, not drift.
- **`agy publish` is a flat command, not a group.** It was the lone `registry add` subcommand,
  so collapsing the group to one verb drops the redundant `add` token (`agy publish <url>`).

---

## 2026-06-25 — Catalog consolidation & link+merge dest templating

**Context:** Merged the skill-registry system (`skills.json` / `registries:` / `agy registry`) into the repository catalog (`repositories.json` / `repositories:` / `agy repo`) as the single name-based resolution path, and added install-time component selection. Paired with link+merge destination templating that namespaces linked dirs per repo+ref.

**Findings:**

- **`RepositoryEntry` is a strict superset of the old `RegistrySkill`.** Any single-skill registry entry is expressible as a repo entry exposing one component (`expose: [{type, name, path|generate}]`). The skill registry was redundant — its only unique value was editorial (curating individual skills vs whole repos), not technical.

- **Convention discovery only scans `skills/<name>/`, not `.claude/skills/<name>/`** (see `discovery._discover_by_convention`). A repo that stores its skill at a non-standard path (e.g. `nextlevelbuilder/ui-ux-pro-max-skill` at `.claude/skills/...`) is **not** auto-discoverable and *must* carry an explicit `path` via an `expose` entry. Same for self-installing tools (graphify) which need `generate`. So `expose` is the declaration vehicle for "what discovery can't infer" — not just curation.

- **`agy add` ref grammar is disambiguated by separators:** a catalog ref never contains `/`, a manual `<source>/<type>/<name>` ref never contains `@`. Routing logic: `@` → catalog ref with component selection; `/` → manual ref; otherwise → bare catalog repo. `--type` (skill/agent/command/hook/mcp) applies only to catalog refs; `@name[,name]` selects specific components (errors on no-match).

- **Interactive picker gating:** the bare-`agy add <repo>` interactive picker is guarded by `sys.stdin.isatty()`. Under pytest/`CliRunner` there's no TTY, so it deterministically installs everything — tests don't need to feed stdin. Use `monkeypatch.setattr(sys.stdin, "isatty", lambda: False)` to make that explicit/robust.

- **Path templating in link+merge profiles uses literal `{key}` replacement, NOT `str.format`.** Rewrite targets embed shell vars like `${CLAUDE_PROJECT_DIR}` that `str.format` misreads as fields. `reconcile._expand` does plain `.replace("{key}", value)`. Available placeholders: `{name}`, `{source}`, `{repo}` (git URL / local path basename, `.git` stripped), `{ref}` (git ref with `/` → `-`). This lets profiles namespace linked dirs as `.claude/hooks/agentry/{repo}@{ref}/{name}` to avoid `{name}` collisions across repos.

- **A changed dest template orphans the old symlink.** When a link+merge `dest` template changes (e.g. adopting `{repo}@{ref}`), `_reconcile_link_merges` must `remove_link(old.link_path)` when `old.link_path != d.link_path`, or stale symlinks accumulate.

- **The single `Registry` model (`{name, location}`) is reused for catalogs.** Don't confuse it with the deleted `RegistrySkill`/`RegistryIndex`. `registry.py` (module name) and the `registry/` directory both survive the rename to "catalog" terminology — only the skill-specific code was removed.

---

## 2026-06-25 — `agy registry add` (catalog authoring)

**Context:** Added `agy registry add <git-url> [name]` to author entries in the curated `registry/repositories.json` from a git/GitHub URL (minimal by default; `--discover` clones and pre-fills `expose`). Note this is *authoring* the catalog, distinct from `agy repo add` which *registers a catalog file* into a project's `.agentry.yml`.

**Findings:**

- **`agy registry` is safe to reuse as a command name.** The old skill-registry `agy registry` group was deleted in the catalog consolidation (commit `cb2f55a`), so the name was free to repurpose for catalog authoring. There is no naming collision.

- **Typer forces argument order: required positionals before optional ones.** The plan called for `add <name> <url>` with an optional derived name, but Typer can't place an optional positional before a required one. Resolved by making the URL the first required arg and `name` the optional second (`add <url> [name]`), deriving the name from the repo basename when omitted.

- **`json.dumps` defaults bite a hand-authored JSON file in two ways.** (1) `ensure_ascii=True` escaped the literal em-dash in arckit's summary to `—` — fixed with `ensure_ascii=False`. (2) `indent=2` reflows pre-existing compact inline arrays (e.g. graphify's `generate` arrays) to one-element-per-line; stdlib `json` has no way to preserve inline-array formatting, so re-serializing any catalog produces cosmetic churn on entries that were hand-compacted. Semantically identical and still loader-valid, but noisy in diffs.

- **`--discover` reuses the resolver/discovery path.** It builds a transient `Source`, calls `resolver.resolve(_root(), source, pinned=None)` (clones into the gitignored `.agentry/sources/<name>`), then `discovery.discover(effective_root(...))`, mapping each `Discovered` → `ExposeEntry(type, name)` — the same machinery `_add_from_catalog` uses, so no new install mechanics.

- **A browser `…/tree/<ref>/<subdir>` URL is parsed for `ref`+`subdir`.** `parse_repo_url` is the repo-URL counterpart to the existing `_normalize_url` (which rewrites *raw-JSON* catalog URLs). Both let the same pasted browser URL serve different inputs; keep them separate.

- **Test gotcha: the `git_source` fixture inits on the default branch, not `main`.** The `--discover` test must `git branch -m main` on the fixture repo so the command's default `--ref main` checks out.

---

## 2026-06-25 — Per-harness hook/MCP fragment routing

**Context:** `agy add superpowers` wrote an invalid `hooks.sessionStart` (camelCase) key into `.claude/settings.json`, which Claude Code rejects. Root-caused and fixed; not a casing bug in agentry.

**Findings:**

- **Convention discovery surfaces *every* `hooks/*.json` as a separate HOOK component, and all of them merge into the single Claude target.** superpowers ships per-harness variants side by side — `hooks/hooks.json` (Claude, `SessionStart`), `hooks/hooks-codex.json` (Codex, `${PLUGIN_ROOT}`), `hooks/hooks-cursor.json` (Cursor, camelCase `sessionStart`). agentry faithfully copied all three into `.claude/settings.json`; the Cursor variant's `sessionStart` is invalid there, and the Codex variant silently collided on `SessionStart` (last-writer-wins in `merge.install_merge`). The manifest recorded all three.

- **Two distinct failure modes need two distinct guards.** A foreign-harness fragment can fail by (a) an *invalid key* (Cursor's `sessionStart`) or (b) a *valid key with wrong content* (Codex's `SessionStart` pointing at `${PLUGIN_ROOT}`/`session-start-codex`). A Claude-event allowlist (`targets.CLAUDE_HOOK_EVENTS` / `filter_claude_hook_events`) only catches (a). Filename-suffix affinity routing (`discovery.harness_suffix`, `<base>-<harness>` → harness, gated to `MERGE_TYPES`) catches both by skipping the variant for any non-matching target. Both layers ship; affinity is the primary fix, the event filter is defense-in-depth.

- **`harness_suffix` must require the hyphen *and* a known slug.** Matching a bare stem would misroute a legit `mcp/codex.json` (an MCP server literally named "codex"). Rule: `name.rpartition("-")` with a non-empty base and suffix ∈ `KNOWN_HARNESS_SLUGS`. Gate it to `MERGE_TYPES` only, or a skill like `using-superpowers` would be wrongly treated as a variant.

- **The fix self-heals already-broken projects with no config migration.** reconcile recomputes affinity from `comp.name` (not a persisted field), so a previously-added `hooks-cursor` component is dropped from the *desired* merge set on the next `sync`. The existing prune path in `_reconcile_merges` (manifest-owned keys absent from desired → `remove_merge`) then deletes the stale `sessionStart` automatically. Verified live: re-running `agy sync` on the affected repo removed both `sessionStart` and the duplicate codex `SessionStart`, keeping only the canonical entry.

- **`agy add` add-time hygiene is separate from reconcile-time correctness.** `_add_from_catalog` now also drops harness-variant components whose harness isn't an active target, so a claude-only install never *records* `hooks-cursor`/`hooks-codex` in `.agentry.yml`. This is purely cosmetic — reconcile already makes them harmless — but keeps the config and interactive picker clean.

---
