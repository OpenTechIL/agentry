# agentry — Command reference

The full `agy` command surface. Run `agy <command> --help` for the canonical, up-to-date flags.
See [README](https://github.com/OpenTechIL/agentry/blob/main/README.md) for the quickstart and [architecture](architecture.md) for the model
behind these commands.

## Project & components

| Command | What it does |
|---|---|
| `agy version` | Print the installed agentry version |
| `agy init [-t TARGET]...` | Create `.agentry.yml`, add `.agentry/` to `.gitignore` |
| `agy list` | Show discovered components grouped by source, with state |
| `agy search [QUERY]` | Search catalogs for repos (filter by QUERY); lists components with no query |
| `agy add <source>/<type>/<name> [--path P]` | Enable a component and install it (`--path` = explicit artifact location) |
| `agy add <repo>[@name[,name]] [--type T]...` | Resolve a catalog repo and install all / selected / by-type components |
| `agy add <ref> --generate-setup CMD --generate-command CMD --produces PATH [--allow-run]` | Install a self-installing tool via its own CLI |
| `agy remove <source>/<type>/<name>` | Remove a component and uninstall it |
| `agy enable <ref>` / `agy disable <ref>` | Toggle a component's `enabled` flag, then sync |
| `agy sync [--allow-run] [--frozen] [--allow-transform]` / `agy install …` | Reconcile on-disk state to config + lock (idempotent). `--allow-run` permits `generate` installers; `--frozen` installs strictly from `.agentry.lock` and fails on drift (CI); `--allow-transform` permits `agent` transforms to run |
| `agy update [SOURCE]` | Re-resolve refs to latest, rewrite `.agentry.lock`, reinstall |
| `agy status` | Report drift between config and what's installed |
| `agy doctor [--strict]` | Preflight: undefined targets, unprovided components, unset `${VARs}`, unsupported combos, drift. Exits 1 on errors (or warnings with `--strict`) |
| `agy why <ref>` | Explain a component: its source + pinned revision and exactly which targets it installs to |
| `agy trust <source>` | Consent for a source to run code at install (generators), pinned to its SHA in the lock. Trusted sources run without `--allow-run`; trust drops if the source moves |
| `agy deps` | Show the resolved dependency map (transitive closure of enabled components) |

## Sources

| Command | What it does |
|---|---|
| `agy source add NAME LOCATION [--ref R] [--local] [--subdir DIR]` | Register a git/local source, download, sync |
| `agy source remove NAME` | Remove a source and uninstall its components |
| `agy source list` | List sources with their locked revision |

## Catalogs

A catalog is a JSON file or URL mapping repo names to their source (and optional curated
components). `catalog add` registers a catalog to **consume**; `catalog add-repo` **authors** an
entry in a catalog file. See [architecture §4](architecture.md#4-source-repo-layout-convention-or-descriptor)
for the catalog schema.

| Command | What it does |
|---|---|
| `agy catalog add NAME LOCATION` | Register a catalog (file or URL) for name-based installs |
| `agy catalog remove NAME` | Remove a catalog (does not uninstall repos already added from it) |
| `agy catalog list` | List configured catalogs and the repos they offer |
| `agy catalog add-repo GIT_URL [NAME] [--ref R] [--subdir DIR] [--summary S] [--discover] [--file F] [--force]` | Add a repo entry to a catalog file (default `registry/repositories.json`); `--discover` pre-fills `expose` |

## Targets (driver overlays)

A *driver overlay* is a named, shareable definition of how some agent installs each component
type — published by a catalog under its `targets` block. Installing one makes an otherwise-
undefined target resolvable without hand-writing `target_profiles`.

| Command | What it does |
|---|---|
| `agy target list` | Show targets in use (resolved via built-in / profile / unresolved) and which overlays are installable from catalogs |
| `agy target add NAME [--catalog C]` | Install a shared driver overlay into `target_profiles`, then sync |

## Interop & portability

| Command | What it does |
|---|---|
| `agy emit agents-md [-o FILE] [--check] [--agent] [--allow-transform] [--yes]` | Compose a portable `AGENTS.md` from your skills/agents/commands. Deterministic by default (`--check` verifies it's current, for CI); `--agent` synthesizes it via your own agent CLI (`transform.command`), gated by `--allow-transform`, with a diff preview + confirm (`--yes` to auto-apply) |
| `agy import apm [--file apm.yml] [--dry-run]` | Translate another agent package manager's manifest into `.agentry.yml` — sources, components, targets, and inline MCP servers — then run `agy sync` |

> Tip: a source repo that ships an `.apm/` package tree is consumable directly — `agy add` /
> `agy list` see its skills/agents/prompts with no republishing.
