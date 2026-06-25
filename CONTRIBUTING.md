# Contributing to agentry

Thanks for helping build a dependency manager for AI agents! This project is
[MIT-licensed](LICENSE); by contributing you agree your work is released under the same terms.

## Dev setup

agentry uses [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/opentech/agentry
cd agentry
uv venv
uv pip install -e ".[dev]"      # editable install + pytest + ruff + pre-commit
uv run pre-commit install       # enable the git hooks (one-time)
uv run agy --help               # smoke test the CLI
uv run pytest                   # run the test suite
```

The `pre-commit` hooks run `ruff format` and `ruff check --fix` (the same rules CI
enforces) plus a few hygiene checks on each commit. Run them across the whole repo
anytime with `uv run pre-commit run --all-files`.

## Project layout

See [docs/architecture.md](docs/architecture.md) for the full design and module map.
The short version:

```
src/agentry/
  cli.py          command surface (Typer)
  models.py       data models (pydantic)
  config.py       .agentry.yml round-trip
  lockfile.py     .agentry.lock
  targets.py      per-tool capability map
  discovery.py    source scanning
  resolver.py     download/checkout sources
  manifest.py     installed-state record
  installers/     link.py (symlink) + merge.py (config inject)
  reconcile.py    the sync engine
tests/            pytest suite
```

## How to add things

**A new target AI tool** (e.g. another editor):
- *End users* need no code — they define it under `target_profiles` in `.agentry.yml`.
- To ship it as a **built-in default**:
  1. Add a `TargetSpec` to `BUILTIN_TARGETS` in `targets.py` (and an id constant on `Target` in `models.py`).
  2. Add a row to the capability table in `docs/architecture.md`.
  3. Cover it in `tests/`.

**A new component type:**
1. Add it to `ComponentType` and to `LINK_TYPES` or `MERGE_TYPES` in `models.py`.
2. Add a `discovery.LAYOUT` entry (where it lives in a source repo).
3. Add a destination for it in each relevant `TargetSpec`.
4. Update docs + tests.

**A new source kind:** add a `SourceType` value and a branch in `resolver.resolve`.

## Source-repo layout (for component authors)

A source repo agentry installs from mirrors the standard agent layout:

```
skills/<name>/        agents/<name>.md      commands/<name>.md
tools/<name>/         hooks/<name>.json     mcp/<name>.json
```

`hooks/*.json` and `mcp/*.json` are JSON **objects of named entries** (see the merge
contract in the architecture doc).

## Tests & linting

- Add tests for any behavior change. The suite uses `tmp_path` fixtures and a tiny local
  git repo (`file://`) — no network required.
- Keep `agy sync` **idempotent** and the **safety invariants** intact (never touch
  unmanaged files/links or hand-added config entries). There are tests guarding both;
  don't weaken them.
- Before opening a PR, run `uv run pytest`, `uvx ruff check .`, and
  `uvx ruff format --check .`. CI runs the same checks on Python 3.10–3.13.

## What CI does

Every push and PR runs **CI** (ruff lint/format + the pytest matrix). On a `vX.Y.Z` tag,
the **Release** workflow builds and publishes to PyPI, and pushes to `main` redeploy the
**docs site** to GitHub Pages. You don't need to do anything beyond opening a green PR.

## Commit & PR conventions

- Small, focused PRs. One behavior change per PR where possible.
- Use clear, imperative commit subjects (`add cursor mcp target`, `fix local symlink drift`).
- Update `docs/architecture.md` when you change behavior — it's the source of truth.

## Code of conduct

Be respectful and constructive. Assume good faith. Harassment of any kind is not tolerated.
This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md) — please read it.
