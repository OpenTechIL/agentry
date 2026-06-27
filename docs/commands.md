# agentry — Command reference

The full `agy` command surface. Run `agy <command> --help` for the canonical, up-to-date flags.
See [README](https://github.com/opentech/agentry/blob/main/README.md) for the quickstart and [architecture](architecture.md) for the model
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
| `agy sync [--allow-run]` / `agy install [--allow-run]` | Reconcile on-disk state to config + lock (idempotent); `--allow-run` permits `generate` installers |
| `agy update [SOURCE]` | Re-resolve refs to latest, rewrite `.agentry.lock`, reinstall |
| `agy status` | Report drift between config and what's installed |
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
