# agentry

[![CI](https://github.com/opentech/agentry/actions/workflows/ci.yml/badge.svg)](https://github.com/opentech/agentry/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/agentry.svg)](https://pypi.org/project/agentry/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)

**A dependency manager for AI coding agents.** `agentry` (command: `agy`) lets you
declare the skills, agents, commands, tools, hooks, and MCP servers your project
uses — then install them into Claude Code, OpenCode, and Cursor with one command.

## Why agentry

The AI ecosystem is expanding without standardization. Today, developers manage AI components
by hand — copying files into `.claude/`, `.opencode/`, `.cursor/` — which means version
conflicts, security risks, and duplicated effort: the same **dependency hell** software solved
decades ago with `pip`, `yarn`, and `uv`.

agentry treats AI components like packages:

- **`.agentry.yml`** — a single, version-controlled file declaring your sources and components.
- **`.agentry.lock`** — exact resolved commit SHAs for **deterministic, reproducible** installs.
- **`.agentry/`** — a local store (git clones / local copies), git-ignored like `node_modules`.
- One **`agy sync`** installs everything into each tool's native layout — via **symlinks**
  (skills/agents/commands/tools) or **reversible config merges** (hooks/MCP).

## Install

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
agy list                                        # see what's available
agy add team-skills/skill/code-reviewer         # enable + install a skill
agy add team-skills/mcp/github                  # merge an MCP server into .mcp.json
agy status                                      # check install state / drift
agy sync                                        # reconcile to match config + lock
```

## Common commands

- `agy init [-t TARGET]...` — create `.agentry.yml` and add `.agentry/` to `.gitignore`.
- `agy source add NAME URL [--ref R] [--subdir DIR]` — register a source, download, sync.
- `agy add <ref>` — enable a component (or whole catalog repo) and install it.
- `agy sync` — reconcile on-disk state to config + lock (idempotent).
- `agy status` — report drift between config and what's installed.
- `agy update [SOURCE]` — re-resolve refs to latest and rewrite `.agentry.lock`.
- `agy version` — print the installed version.

**Full command reference → [docs/commands.md](docs/commands.md).**

## How install works

| Component type | Strategy | Destination (Claude Code example) |
|---|---|---|
| `skill` | symlink | `.claude/skills/<name>/` |
| `agent` | symlink | `.claude/agents/<name>.md` |
| `command` | symlink | `.claude/commands/<name>.md` |
| `tool` | symlink | `.claude/tools/<name>/` |
| `hook` | config merge | `.claude/settings.json` → `hooks` |
| `mcp` | config merge | `.mcp.json` → `mcpServers` |

File/dir components install via **symlink** by default (live-updating into the `.agentry/`
store); switch any to a committable real copy with `strategy: copy`. Target support varies by
tool (e.g. Cursor is rules-only); unsupported combinations are skipped with a warning.

Both sides of the mapping are data-driven: a source repo can self-describe its layout
(`agentry.yaml`), components can declare recursive version-aware `requires`, tool-specific
hook/MCP fragments route by an `-<harness>` suffix, and you can override paths or define a
**brand-new AI tool** entirely in `.agentry.yml` under `target_profiles` — no code.
See [docs/architecture.md](docs/architecture.md) for the full capability map, descriptor schema,
and safety model.

## Installing third-party skills

Most skills on GitHub don't follow agentry's `skills/<name>/` layout. Three ways to install them:

1. **Direct-from-repo (`--path`)** — when the repo *is* a skill (its root holds `SKILL.md`) or
   keeps it at an arbitrary path:

   ```bash
   agy source add cool https://github.com/some/cool-skill
   agy add cool/skill/cool-skill --path .          # or --path packages/my-skill
   ```

2. **Self-installing tools (`generate`)** — some skills ship no skill file and generate one via
   their own CLI. Declare the commands and the files they produce; running them is opt-in
   (`--allow-run`):

   ```bash
   agy add graphify/skill/graphify \
     --generate-setup "uv tool install graphifyy" \
     --generate-command "graphify install --project" \
     --produces ".claude/skills/graphify"
   agy sync --allow-run
   ```

3. **Catalogs (name-based, the "artifactory" model)** — a catalog is a JSON file or URL mapping
   repo names to their source, so you install by name without knowing the URL or flags:

   ```bash
   agy catalog add default https://catalog.example.com/repositories.json
   agy add arckit                   # whole repo: every component it provides
   agy add arckit --type skill      # only skills (repeatable)
   agy add arckit@code-review,lint  # only the named components
   ```

   A **starter catalog** ships at [`registry/repositories.json`](registry/repositories.json) with
   four curated repos — `arckit`, `ui-ux-pro-max`, `graphify`, and `superpowers`. Point a catalog
   at it and install by name. The catalog schema (including the `copy` and `namespaced` per-repo
   flags) is documented in [docs/architecture.md](docs/architecture.md#4-source-repo-layout--convention-or-descriptor).

## Contribute a repo to the starter catalog

Want a repo added to [`registry/repositories.json`](registry/repositories.json)? Two ways:

- **Open a PR** — clone this repo, then run `agy catalog add-repo <git-url> [--summary "…"] [--discover]`
  (or hand-edit the JSON), commit, and open a pull request. A `…/tree/<ref>/<subdir>` URL infers
  the ref and subdir; `--discover` pre-fills the components. See the
  [PR template](.github/PULL_REQUEST_TEMPLATE.md).
- **Request via an issue** — prefer not to open a PR? [File an issue](https://github.com/opentech/agentry/issues)
  with the repo URL and a one-line summary, and a maintainer will add it.

## Documentation

- [Commands](docs/commands.md) — the full `agy` command reference.
- [Architecture](docs/architecture.md) — design, config/lock/manifest model, reconcile flow, safety.
- [Knowledge base](docs/knowledge-base.md) — project-specific pitfalls, patterns, and discoveries.
- [Changelog](CHANGELOG.md) — notable changes per release.
- [Branding kit](docs/branding-kit.md) — name, identity, CLI tone of voice.
- [Contributing](CONTRIBUTING.md) — dev setup, adding targets/component types, tests.
- [Code of Conduct](CODE_OF_CONDUCT.md) — community standards.

## Contributing

Contributions are very welcome — new targets, component types, catalog entries, docs, and bug
fixes.

```bash
git clone https://github.com/opentech/agentry && cd agentry
uv venv && uv pip install -e ".[dev]"   # editable install + test/lint tooling
uv run pre-commit install               # format & lint on every commit
uv run pytest                           # run the suite
```

CI runs `ruff` and the `pytest` matrix on Python 3.10–3.13; keeping `agy sync` idempotent and the
safety invariants intact is the one hard rule. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full
guide and the [Code of Conduct](CODE_OF_CONDUCT.md) before you start.

## License

[MIT](LICENSE) © 2026 OpenTech.
