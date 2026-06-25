# Knowledge Base

Project-specific pitfalls, patterns, constraints, and discoveries captured during development sessions.

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
