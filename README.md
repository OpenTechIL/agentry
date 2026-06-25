# agentry

[![CI](https://github.com/opentech/agentry/actions/workflows/ci.yml/badge.svg)](https://github.com/opentech/agentry/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/agentry.svg)](https://pypi.org/project/agentry/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)

**A dependency manager for AI coding agents.** `agentry` (command: `agy`) lets you
declare the skills, agents, commands, tools, hooks, and MCP servers your project
uses — then install them into Claude Code, OpenCode, and Cursor with one command.

## The problem

The AI ecosystem is expanding without standardization. Today, developers manage a
fragmented web of AI components by hand — copying files into `.claude/`, `.opencode/`,
`.cursor/`. The result is version conflicts, security risks, duplicated effort, and
maintenance overhead: the same **dependency hell** software solved decades ago with
`pip`, `yarn`, and `uv`.

## The solution

Treat AI components like packages:

- **`.agentry.yml`** — a single, version-controlled file declaring your sources and components.
- **`.agentry.lock`** — exact resolved commit SHAs for **deterministic, reproducible** installs across dev and prod.
- **`.agentry/`** — a local store (git clones / local copies), git-ignored like `node_modules`.
- One `agy sync` installs everything into each tool's native layout — via **symlinks**
  (skills/agents/commands/tools) or **reversible config merges** (hooks/MCP).

## Install & run

No global install needed — run straight from git with [`uv`](https://docs.astral.sh/uv/):

```bash
uvx --from git+https://github.com/opentech/agentry agy <command>
```

Or install into a project/venv:

```bash
uv pip install agentry        # then: agy <command>
```

## Quickstart

```bash
agy init --target claude --target opencode      # create .agentry.yml + .gitignore
agy source add team-skills https://github.com/org/team-skills --ref main
# monorepo? point at the subdir that holds skills/ agents/ commands/ …:
agy source add arckit https://github.com/tractorjuice/arc-kit --subdir plugins/arckit-claude
agy list                                        # see what's available
agy add team-skills/skill/code-reviewer         # enable + install a skill
agy add team-skills/mcp/github                  # merge an MCP server into .mcp.json
# a repo that *is* a skill (no skills/<name>/ layout)? point at its root:
agy source add cool https://github.com/some/cool-skill
agy add cool/skill/cool-skill --path .
# or resolve a repo by name from a catalog (see "Third-party skills" below):
agy catalog add default https://catalog.example.com/repositories.json
agy add graphify                                # whole repo, or pick: arckit@code-review --type skill
agy status                                      # check install state / drift
agy disable team-skills/mcp/github              # uninstall, keep the declaration
agy sync                                        # reconcile to match config + lock
agy update                                      # re-resolve refs, rewrite the lock
```

## Command reference

| Command | What it does |
|---|---|
| `agy init [-t TARGET]...` | Create `.agentry.yml`, add `.agentry/` to `.gitignore` |
| `agy source add NAME LOCATION [--ref R] [--local] [--subdir DIR]` | Register a git/local source, download, sync |
| `agy source remove NAME` | Remove a source and uninstall its components |
| `agy source list` | List sources with their locked revision |
| `agy catalog add NAME LOCATION` | Register a catalog (file or URL) for name-based installs |
| `agy catalog remove NAME` / `catalog list` | Manage catalogs; list offered repos |
| `agy publish GIT_URL [NAME] [--discover] [--file F]...` | Author a catalog: add a repo entry to `registry/repositories.json` |
| `agy list` / `agy search [QUERY]` | Show discovered components + catalog repos (filter by QUERY) |
| `agy add <source>/<type>/<name> [--path P]` | Enable a component and install it (`--path` = explicit artifact location) |
| `agy add <repo>[@name[,name]] [--type T]...` | Resolve a catalog repo and install all / selected / by-type components |
| `agy sync --allow-run` | Sync, permitting `generate` components to run their own installer |
| `agy remove <source>/<type>/<name>` | Remove a component and uninstall it |
| `agy enable / disable <ref>` | Toggle a component's `enabled` flag, then sync |
| `agy sync` / `agy install` | Reconcile on-disk state to config + lock (idempotent) |
| `agy update [SOURCE]` | Re-resolve refs to latest, rewrite `.agentry.lock`, reinstall |
| `agy status` | Report drift between config and what's installed |
| `agy deps` | Show the resolved dependency map (transitive closure of enabled components) |

## How a component is installed

| Component type | Strategy | Destination (Claude Code example) |
|---|---|---|
| `skill` | symlink | `.claude/skills/<name>/` |
| `agent` | symlink | `.claude/agents/<name>.md` |
| `command` | symlink | `.claude/commands/<name>.md` |
| `tool` | symlink | `.claude/tools/<name>/` |
| `hook` | config merge | `.claude/settings.json` → `hooks` |
| `mcp` | config merge | `.mcp.json` → `mcpServers` |

File/dir components install via **symlink** by default (live-updating, points back into
the `.agentry/` store). Switch any of them to **copy** — a self-contained, committable real
file/dir — by setting `strategy: copy` in a `target_profiles` rule, or per catalog repo with
the `copy` flag (see [Third-party skills](#third-party-skills)).

Target support varies by tool (e.g. Cursor is rules-only); unsupported combinations
are skipped with a warning.

**Per-harness config variants.** A repo may ship tool-specific hook/MCP fragments side
by side — e.g. `hooks/hooks.json` (Claude), `hooks/hooks-cursor.json` (Cursor),
`hooks/hooks-codex.json` (Codex). agentry reads the `-<harness>` suffix and routes each
variant **only** to its matching target, so a Cursor or Codex fragment never lands in
Claude's `settings.json`. The canonical, suffix-less file applies to every target that
supports the type. As a final guard, a hook event Claude Code doesn't recognize is
dropped from `.claude/settings.json` with a warning rather than written out.

### Configurable mappings

Both sides of the mapping are data-driven:

- **Source layout** — a source repo can ship an optional `agentry.yaml` describing where
  its components live (explicit `path` or `glob`); without it, agentry scans the standard
  `skills/`, `agents/`, … convention. For a **monorepo** that nests its components (e.g. a
  plugin marketplace), pass `--subdir DIR` on `agy source add` (or set `subdir:` on the
  source in `.agentry.yml`) — discovery and convention scanning then start from that subdir.
- **Dependencies** — a component can declare what it needs via `requires` in the source's
  `agentry.yaml`. Dependencies resolve **recursively** and are **version-aware**: a `url`
  dependency on another repo is pulled into `.agentry.lock` (not `.agentry.yml`), the full
  closure is walked breadth-first, and conflicting version pins abort with a clear error.
  Run `agy deps` to see the resolved map.
- **Target destinations** — override a built-in path or define a **brand-new AI tool**
  entirely in `.agentry.yml` under `target_profiles` (no code):

  ```yaml
  targets: [claude, mycli]
  target_profiles:
    mycli:
      skill: { strategy: link,  dest: ".mycli/skills/{name}" }   # quote {name}!
      mcp:   { strategy: merge, file: ".mycli/config.json", pointer: "mcpServers" }
  ```

  Destination templates expand `{name}` plus, for link/link+merge, `{source}` (the
  configured source name), `{repo}` (the repo basename), and `{ref}` (the git ref, with
  `/` flattened to `-`). Namespace per repo+ref to avoid collisions across plugins —
  e.g. a `link+merge` hook `dest` of `.claude/hooks/agentry/{repo}@{ref}/{name}`.

See [docs/architecture.md](docs/architecture.md) for the full capability map, the
descriptor schema, and the safety model.

## Third-party skills

Most skills you'll find on GitHub don't follow agentry's `skills/<name>/` layout. Three
ways to install them, project-local into `.claude/skills/`:

1. **Direct-from-repo (`--path`)** — when the repo *is* a skill (its root holds `SKILL.md`)
   or keeps it at an arbitrary path:

   ```bash
   agy source add cool https://github.com/some/cool-skill
   agy add cool/skill/cool-skill --path .          # or --path packages/my-skill
   ```
   This is the ordinary symlink install — idempotent, reversible, never touches files it
   doesn't own.

2. **Self-installing tools (`generate`)** — some skills (e.g.
   [graphify](https://github.com/safishamsi/graphify)) ship no skill file and instead
   generate one via their own CLI. Declare the commands and the files they produce:

   ```bash
   agy source add graphify https://github.com/safishamsi/graphify
   agy add graphify/skill/graphify \
     --generate-setup   "uv tool install graphifyy" \
     --generate-command "graphify install --project" \
     --produces ".claude/skills/graphify"
   agy sync --allow-run        # prints the commands, runs them, records produced files
   ```
   **Running third-party commands is opt-in.** Without `--allow-run`, `sync` prints the
   exact commands and skips them. `agy remove` deletes only the recorded `--produces`
   paths. (graphify's exact PyPI name/flags may differ — check its README.)

3. **Catalogs (name-based, the "artifactory" model)** — a catalog is a JSON file or URL
   mapping repo names to their source (and optional curated components), so you don't need to
   know the repo URL or flags. `agy add <repo>` resolves and installs; you choose what:

   ```bash
   agy catalog add default https://catalog.example.com/repositories.json
   agy search graph                 # browse offered repos
   agy add arckit                   # whole repo: every component it provides
   agy add arckit --type skill      # only skills (repeatable: --type command …)
   agy add arckit@code-review,lint  # only the named components
   ```

   Selection: a bare `agy add <repo>` in a terminal opens an interactive picker; with no TTY
   it installs everything. `--type` (skill/agent/command/hook/mcp) and `@name` narrow it.

   A **starter catalog** ships in this repo at
   [`registry/repositories.json`](registry/repositories.json) with curated repos (e.g.
   [`ui-ux-pro-max`](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill), `graphify`,
   `arckit`). Point a catalog at it and install by name:

   ```bash
   agy catalog add curated /path/to/agentry/registry/repositories.json
   agy add ui-ux-pro-max     # link-installs from .claude/skills/ui-ux-pro-max in that repo
   ```

   **Authoring a catalog** — add an entry from a git/GitHub URL with `agy publish`
   (writes to [`registry/repositories.json`](registry/repositories.json) by default, override
   with `--file`). A browser `…/tree/<ref>/<subdir>` URL infers the `ref` and `subdir`; the
   name defaults to the repo basename. `--discover` clones the repo and pre-fills `expose`
   from the components it finds:

   ```bash
   agy publish https://github.com/safishamsi/graphify --summary "knowledge graph"
   agy publish https://github.com/tractorjuice/arc-kit/tree/main/plugins/arckit-claude
   agy publish https://github.com/safishamsi/graphify --discover   # fill `expose`
   ```

   The catalog is plain JSON — the same shape a hosted catalog server would serve, so a
   local file and a future server are interchangeable. A conventional-layout repo needs only
   a `source`; `expose` declares curated components (and carries the `path`/`generate` for
   artifacts discovery can't infer). Two optional per-repo flags shape the install layout at
   `agy add` time:

   - `"copy": true` — install this repo's file/dir components by **copying** instead of
     symlinking (real files, committable; default `false`).
   - `"namespaced": true` (the **default**) — nest **commands** and **agents** under a
     `<repo>/` subfolder, so a plugin's slash commands are namespaced
     (`.claude/commands/<repo>/adr.md` → `/<repo>:adr`). Skills stay flat (Claude Code only
     discovers `.claude/skills/<name>/SKILL.md`). Set `"namespaced": false` for a flat layout.

   ```json
   {
     "version": 1,
     "repositories": {
       "arckit": {
         "summary": "Architecture governance toolkit (skills, agents, commands, …)",
         "source": { "type": "git", "url": "https://github.com/tractorjuice/arc-kit", "ref": "main", "subdir": "plugins/arckit-claude" }
       },
       "graphify": {
         "summary": "Codebase → knowledge graph",
         "source": { "type": "git", "url": "https://github.com/safishamsi/graphify", "ref": "main" },
         "expose": [
           {
             "type": "skill",
             "name": "graphify",
             "generate": {
               "setup":   [["uv", "tool", "install", "graphifyy"]],
               "command": ["graphify", "install", "--project"],
               "produces": [".claude/skills/graphify"]
             }
           }
         ]
       }
     }
   }
   ```

## Documentation

- [Architecture](docs/architecture.md) — design, config/lock/manifest model, reconcile flow, safety.
- [Knowledge base](docs/knowledge-base.md) — project-specific pitfalls, patterns, and discoveries.
- [Changelog](CHANGELOG.md) — notable changes per release.
- [Branding kit](docs/branding-kit.md) — name, identity, CLI tone of voice.
- [Contributing](CONTRIBUTING.md) — dev setup, adding targets/component types, tests.
- [Code of Conduct](CODE_OF_CONDUCT.md) — community standards.

## Contributing

Contributions are very welcome — new targets, component types, catalog entries, docs, and
bug fixes. The short version:

```bash
git clone https://github.com/opentech/agentry && cd agentry
uv venv && uv pip install -e ".[dev]"   # editable install + test/lint tooling
uv run pre-commit install               # format & lint on every commit
uv run pytest                           # run the suite
```

Open a small, focused PR — the [PR template](.github/PULL_REQUEST_TEMPLATE.md) has the
checklist. CI runs `ruff` (lint + format) and the `pytest` matrix on Python 3.10–3.13;
keeping `agy sync` idempotent and the safety invariants intact is the one hard rule. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the full guide and the
[Code of Conduct](CODE_OF_CONDUCT.md) before you start.

## License

[MIT](LICENSE) © 2026 agentry contributors.
