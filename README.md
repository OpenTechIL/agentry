# agentry

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

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
# or resolve a skill by name from a registry (see "Third-party skills" below):
agy registry add default https://skills.example.com/index.json
agy add graphify
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
| `agy registry add NAME LOCATION` | Register a skill index (file or URL) for name-based installs |
| `agy registry remove NAME` / `registry list` | Manage registries; list offered skills |
| `agy list` / `agy search [QUERY]` | Show discovered components + registry skills (filter by QUERY) |
| `agy add <source>/<type>/<name> [--path P]` | Enable a component and install it (`--path` = explicit artifact location) |
| `agy add <skill-name>` | Resolve a bare skill name from the registries and install it |
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

Target support varies by tool (e.g. Cursor is rules-only); unsupported combinations
are skipped with a warning.

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

3. **Registries (name-based, the "artifactory" model)** — a registry is an index file or
   URL mapping skill names to their source + install method, so you don't need to know the
   repo URL or flags:

   ```bash
   agy registry add default https://skills.example.com/index.json
   agy search graph          # browse offered skills
   agy add graphify          # resolves source + install method from the index
   ```

   A **starter index** ships in this repo at [`registry/skills.json`](registry/skills.json)
   with curated skills (e.g. [`ui-ux-pro-max`](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill),
   `graphify`). Point a registry at it and install by name:

   ```bash
   agy registry add curated /path/to/agentry/registry/skills.json
   agy add ui-ux-pro-max     # link-installs from .claude/skills/ui-ux-pro-max in that repo
   ```

   The index is plain JSON — the same shape a hosted registry server would serve, so a
   local file and a future server are interchangeable:

   ```json
   {
     "version": 1,
     "skills": {
       "graphify": {
         "summary": "Codebase → knowledge graph",
         "source": { "type": "git", "url": "https://github.com/safishamsi/graphify", "ref": "main" },
         "install": "generate",
         "generate": {
           "setup":   ["uv tool install graphifyy"],
           "command": ["graphify", "install", "--project"],
           "produces": [".claude/skills/graphify"]
         }
       },
       "cool-skill": {
         "source": { "type": "git", "url": "https://github.com/some/cool-skill", "ref": "v1" },
         "install": "link",
         "path": "."
       }
     }
   }
   ```

## Documentation

- [Architecture](docs/architecture.md) — design, config/lock/manifest model, reconcile flow, safety.
- [Branding kit](docs/branding-kit.md) — name, identity, CLI tone of voice.
- [Contributing](CONTRIBUTING.md) — dev setup, adding targets/component types, tests.

## License

[MIT](LICENSE) © 2026 agentry contributors.
