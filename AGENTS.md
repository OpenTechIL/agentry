# AGENTS.md

Instructions for AI coding agents (Claude Code, Cursor, OpenCode, …) working in this
repository. Read this before making changes. Humans: see [README.md](README.md) and
[CONTRIBUTING.md](CONTRIBUTING.md) — this file restates the parts an agent needs and adds
the conventions to follow when editing the code.

## What this project is

`agentry` (CLI command `agy`) is a **dependency manager for AI coding agents**. It lets a
project declare skills, agents, commands, tools, hooks, and MCP servers in `.agentry.yml`,
pin them in `.agentry.lock`, and install them into each tool's native layout (`.claude/`,
`.cursor/`, `.gemini/`, `.opencode/`, … — seven agents ship built-in, and more can be added
from config via `target_profiles`) with one `agy sync`. Write once, deploy to any agent.

Python package, `src/` layout, built with `hatchling`, managed with [`uv`](https://docs.astral.sh/uv/).

## Golden rules

- **Read [docs/architecture.md](docs/architecture.md) first.** It is the source of truth for
  the data model, reconcile flow, and safety invariants. Update it in the same change that
  alters behavior.
- **Never weaken the safety invariants.** `agy sync` must (1) stay **idempotent** — running
  it twice changes nothing the second time — and (2) **never touch unmanaged files, symlinks,
  or hand-added config entries**. Tests guard both; do not edit those tests to make a change
  pass.
- **Both sides of the install mapping are data-driven.** Source layout and target
  destinations are configuration (`agentry.yaml` descriptors, `target_profiles`), not
  hardcoded paths. Add capability through data/specs, not special-cases.
- Don't add a dependency without a clear reason; runtime deps are deliberately minimal
  (`typer`, `rich`, `pydantic`, `ruamel.yaml`).

## Environment & setup

```bash
uv venv
uv pip install -e ".[dev]"      # editable install + pytest
uv run agy --help               # smoke-test the CLI
```

Requires Python ≥ 3.10. The CLI entry point is `agy = "agentry.cli:app"`.

## How to develop

Run any command in the project venv with `uv run`:

```bash
uv run agy <command>            # exercise the CLI
uv run python -c "import agentry"
```

Module map (`src/agentry/`):

| Module | Responsibility |
|---|---|
| `cli.py` | Command surface (Typer). User-facing commands and output. |
| `models.py` | Data models (pydantic): `Target`, `ComponentType`, `TargetSpec`, `LINK_TYPES`/`MERGE_TYPES`. |
| `config.py` | `.agentry.yml` read/write (round-trip preserving). |
| `lockfile.py` | `.agentry.lock` read/write. |
| `targets.py` | `BUILTIN_TARGETS` — per-tool capability map. |
| `discovery.py` | Scans a source repo for components (`LAYOUT` + descriptor). |
| `resolver.py` | Downloads/checks out sources (git / local). |
| `manifest.py` | Records installed state. |
| `deps.py` | Dependency handling between components. |
| `gitignore.py` | Manages `.agentry/` ignore entries. |
| `installers/link.py` | Symlink installs (skills/agents/commands/tools). |
| `installers/merge.py` | Reversible config merges (hooks/MCP). |
| `reconcile.py` | The sync engine — drives config + lock → on-disk state. |

When adding capability, prefer the patterns already documented in
[CONTRIBUTING.md](CONTRIBUTING.md#how-to-add-things): a new target tool, a new component
type, or a new source kind each have a defined sequence (touch `models.py` / `targets.py` /
`discovery.py`, then docs + tests).

## How to test

The suite is `pytest`, lives in `tests/`, and **requires no network** — it uses `tmp_path`
fixtures and a tiny local `file://` git repo.

```bash
uv run pytest                   # full suite (configured with -q)
uv run pytest tests/test_reconcile.py          # one file
uv run pytest -k idempotent -v                 # one pattern
uv run pytest --cov=agentry                    # with coverage (pytest-cov)
```

Testing rules:

- **Add a test for every behavior change.** No behavior change ships without a test.
- The idempotency and safety-invariant tests (`test_reconcile.py` and friends) are
  load-bearing — extend them, don't relax them.
- Keep tests hermetic: no real network, no writing outside `tmp_path`, no dependence on the
  developer's `~/.claude`.
- Run the **full suite** before opening a PR.

## How to contribute

1. Branch off `main`; keep PRs small and focused — one behavior change per PR where possible.
2. Make the change in `src/agentry/`, add/adjust tests, and **update
   [docs/architecture.md](docs/architecture.md)** if behavior changed.
3. `uv run pytest` must pass; `uv run agy --help` must still work.
4. Imperative commit subjects, lower-case, no trailing period:
   `add cursor mcp target`, `fix local symlink drift`.
5. Open the PR with a short rationale and note any safety/idempotency considerations.

## Tips & best practices

- **Quote `{name}` in YAML paths.** In `target_profiles` a `dest:` like
  `".mycli/skills/{name}"` must be quoted, or YAML parses `{…}` as a mapping.
- **Symlink vs. merge:** symlink component types (`skill`, `agent`, `command`, `tool`) are
  removed cleanly on uninstall; merge types (`hook`, `mcp`) inject named entries into a
  config file and must be **reversibly** removable — only ever touch entries agentry added.
- **Unsupported target/type combinations are skipped with a warning, not an error** (e.g.
  Cursor is rules-only). Preserve that behavior when extending the capability map.
- **`hooks/*.json` and `mcp/*.json` in a source repo are objects of named entries**, not
  arrays — see the merge contract in the architecture doc.
- For a monorepo source, components may live under a `--subdir`; discovery starts from there.
  Don't assume components sit at the repo root.
- Keep CLI output going through `rich` and consistent with the existing tone (see
  [docs/branding-kit.md](docs/branding-kit.md)).
- Prefer extending `BUILTIN_TARGETS` / descriptors over branching on a specific tool name in
  logic.

## Where to add new tips

Found a non-obvious gotcha while working here? Add it to the **Tips & best practices**
section above in the same PR, keeping each entry to one or two lines and tied to something
concrete in the code. Larger design rationale belongs in
[docs/architecture.md](docs/architecture.md), not here.
