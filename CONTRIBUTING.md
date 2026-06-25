# Contributing to agentry

Thanks for helping build a dependency manager for AI agents! This project is
[MIT-licensed](LICENSE); by contributing you agree your work is released under the same terms.

## Dev setup

agentry uses [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/opentech/agentry
cd agentry
uv venv
uv pip install -e ".[dev]"      # editable install + pytest
uv run agy --help               # smoke test the CLI
uv run pytest                   # run the test suite
```

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

## Tests

- Add tests for any behavior change. The suite uses `tmp_path` fixtures and a tiny local
  git repo (`file://`) — no network required.
- Keep `agy sync` **idempotent** and the **safety invariants** intact (never touch
  unmanaged files/links or hand-added config entries). There are tests guarding both;
  don't weaken them.
- Run `uv run pytest` before opening a PR.

## Commit & PR conventions

- Small, focused PRs. One behavior change per PR where possible.
- Use clear, imperative commit subjects (`add cursor mcp target`, `fix local symlink drift`).
- Update `docs/architecture.md` when you change behavior — it's the source of truth.

## Code of conduct

Be respectful and constructive. Assume good faith. Harassment of any kind is not tolerated.
