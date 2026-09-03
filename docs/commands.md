# agentry — Command reference

The full `agentry` command surface. Run `agentry <command> --help` for the canonical, up-to-date flags.
See [README](https://github.com/OpenTechIL/agentry/blob/main/README.md) for the quickstart and [architecture](architecture.md) for the model
behind these commands.

## Project & components

| Command | What it does |
|---|---|
| `agentry version` | Print the installed agentry version |
| `agentry init [-t TARGET]... [--no-default-catalog]` | Create `.agentry.yml`, add `.agentry/` to `.gitignore`, register the default `agentry` catalog (skip with `--no-default-catalog`) |
| `agentry list` | Show discovered components grouped by source, with state |
| `agentry search [QUERY]` | Search catalogs for repos (filter by QUERY); lists components with no query |
| `agentry add <source>/<type>/<name> [--path P]` | Enable a component and install it (`--path` = explicit artifact location) |
| `agentry add <repo>[@name[,name]] [--type T]...` | Resolve a catalog repo and install all / selected / by-type components |
| `agentry add <ref> --generate-setup CMD --generate-command CMD --produces PATH [--allow-run]` | Install a self-installing tool via its own CLI |
| `agentry remove <source>/<type>/<name>` | Remove a component and uninstall it |
| `agentry enable <ref>` / `agentry disable <ref>` | Toggle a component's `enabled` flag, then sync |
| `agentry sync [--allow-run] [--frozen] [--allow-transform]` / `agentry install …` | Reconcile on-disk state to config + lock (idempotent). `--allow-run` permits `generate` installers; `--frozen` installs strictly from `.agentry.lock` and fails on drift (CI); `--allow-transform` permits `agent` transforms to run |
| `agentry update [SOURCE]` | Re-resolve refs to latest, rewrite `.agentry.lock`, reinstall |
| `agentry status` | Report drift between config and what's installed |
| `agentry doctor [--strict]` | Preflight: undefined targets, unprovided components, unset `${VARs}`, unsupported combos, drift. Exits 1 on errors (or warnings with `--strict`) |
| `agentry why <ref>` | Explain a component: its source + pinned revision and exactly which targets it installs to |
| `agentry trust <source>` | Consent for a source to run code at install (generators), pinned to its SHA in the lock. Trusted sources run without `--allow-run`; trust drops if the source moves |
| `agentry deps` | Show the resolved dependency map (transitive closure of enabled components) |

## Sources

| Command | What it does |
|---|---|
| `agentry source add NAME LOCATION [--ref R] [--local] [--subdir DIR]` | Register a git/local source, download, sync |
| `agentry source remove NAME` | Remove a source and uninstall its components |
| `agentry source list` | List sources with their locked revision |

## Catalogs

A catalog is a JSON file or URL mapping repo names to their source (and optional curated
components). `catalog add` registers a catalog to **consume**; `catalog add-repo` **authors** an
entry in a catalog file. See [architecture §4](architecture.md#4-source-repo-layout-convention-or-descriptor)
for the catalog schema.

| Command | What it does |
|---|---|
| `agentry catalog add NAME LOCATION` | Register a catalog (file or URL) for name-based installs |
| `agentry catalog remove NAME` | Remove a catalog (does not uninstall repos already added from it) |
| `agentry catalog list` | List configured catalogs and the repos they offer |
| `agentry catalog add-repo GIT_URL [NAME] [--ref R] [--subdir DIR] [--summary S] [--discover] [--file F] [--force]` | Add a repo entry to a catalog file (default `registry/repositories.json`); `--discover` pre-fills `expose` |

## Targets (driver overlays)

A *driver overlay* is a named, shareable definition of how some agent installs each component
type — published by a catalog under its `targets` block. Installing one makes an otherwise-
undefined target resolvable without hand-writing `target_profiles`.

| Command | What it does |
|---|---|
| `agentry target list` | Show targets in use (resolved via built-in / profile / unresolved) and which overlays are installable from catalogs |
| `agentry target add NAME [--catalog C]` | Install a shared driver overlay into `target_profiles`, then sync |

## Interop & portability

| Command | What it does |
|---|---|
| `agentry emit agents-md [-o FILE] [--check] [--agent] [--allow-transform] [--yes]` | Compose a portable `AGENTS.md` from your skills/agents/commands. Deterministic by default (`--check` verifies it's current, for CI); `--agent` synthesizes it via your own agent CLI (`transform.command`), gated by `--allow-transform`, with a diff preview + confirm (`--yes` to auto-apply) |
| `agentry emit triggers [--check] [-o FILE]` | Register a skill-trigger block into every active target's memory file (`.claude/CLAUDE.md`, `AGENTS.md`, …). `--check` verifies they're current, for CI; `-o FILE` writes one explicit file instead of fanning out |
| `agentry import apm [--file apm.yml] [--dry-run]` | Translate another agent package manager's manifest into `.agentry.yml` — sources, components, targets, and inline MCP servers — then run `agentry sync` |

> Tip: a source repo that ships an `.apm/` package tree is consumable directly — `agentry add` /
> `agentry list` see its skills/agents/prompts with no republishing.

### `agentry emit triggers` — skill triggers into memory files

Many harnesses don't *auto-load* an installed skill; they only invoke it if the always-loaded
instruction/memory file tells them when. `agentry emit triggers` composes one bullet per installed
skill — its name mapped to its `SKILL.md` `description` (the "use when …" trigger) — and splices
that list into **every active target's memory file**, the way `agentry sync` fans installs out:

| Target | Memory file |
|---|---|
| claude | `.claude/CLAUDE.md` |
| codex · opencode · kimi · `agents` | `AGENTS.md` |
| gemini | `GEMINI.md` |
| copilot | `.github/copilot-instructions.md` |
| cursor | `.cursor/rules/agentry-triggers.mdc` |
| windsurf | `.windsurf/rules/agentry-triggers.md` |
| kiro | `.kiro/steering/agentry-triggers.md` |

The block is delimited by markers so only it is written — everything else in the file is left
intact — and the merge is idempotent (same skills + descriptions → byte-identical output), so
committing the result and running `--check` in CI is safe:

```markdown
<!-- BEGIN agentry:triggers -->
<!-- Managed by agentry; edits between these markers are overwritten. Run `agentry emit triggers` to refresh. -->
## Agentry-managed skills

Auto-invoke a skill below when the situation matches its trigger:

- **code-reviewer** — Use when reviewing a pull request or a diff before merging.
- **pdf-processing** — Use when extracting text or tables from PDF files.
<!-- END agentry:triggers -->
```

Only `skill` components are listed — agents and commands are invoked explicitly, not
auto-triggered. Pass `-o FILE` to write a single explicit file instead of fanning out.
