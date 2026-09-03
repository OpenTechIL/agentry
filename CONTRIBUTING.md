# Contributing to agentry

Thanks for helping build a dependency manager for AI agents! This project is
[MIT-licensed](LICENSE); by contributing you agree your work is released under the same terms.

## Dev setup

agentry uses [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/OpenTechIL/agentry
cd agentry
uv venv
uv pip install -e ".[dev]"      # editable install + pytest + ruff + pre-commit
uv run pre-commit install       # enable the git hooks (one-time)
uv run agentry --help               # smoke test the CLI
uv run pytest                   # run the test suite
```

Other extras, if you need them: `.[docs]` for `uv run mkdocs serve` (live preview of the
docs site on <http://127.0.0.1:8000>), `.[build]` for `pyinstaller` (frozen binaries),
`.[tui]` for the optional Textual UI, `.[lint]` for ruff alone.

The CLI installs under three names — `agentry` (canonical), plus the short aliases `agy`
and `agyx`. `agy` is also Google's Antigravity CLI command; use `agentry` in docs, tests
and commit messages.

The `pre-commit` hooks run `ruff format` and `ruff check --fix` (the same rules CI
enforces) plus a few hygiene checks on each commit. Run them across the whole repo
anytime with `uv run pre-commit run --all-files`.

## Project layout

[docs/architecture.md §8](docs/architecture.md#8-module-map) has the full design and the
**single** module map. It used to be duplicated here and in `AGENTS.md`; both copies went
stale, so they now link there instead.


agentry has **two sides**. The *source side* (`discovery.py`) is canonical — a component
is authored once. The *target side* (`drivers/`) maps those components into each AI agent.
Adding support for a new agent means adding a driver; it never touches the source side.

## Adding a driver for a new agent

A **driver** ([`drivers/<agent>.py`](src/agentry/drivers)) represents one kind of AI agent
(Claude Code, Gemini CLI, …). For a one-off, end users need no code — they define the tool
under `target_profiles` in `.agentry.yml`. To ship it as a **built-in**:

1. Create `src/agentry/drivers/<agent>.py` exposing a `DRIVER` — a `Driver` wrapping a
   `TargetSpec` (per component type, a link/copy `dest` template or a `MergeDest`). Map only
   what installs cleanly with the current strategies; omit a type the agent doesn't support
   or whose format agentry can't yet write (it'll be skipped, not broken). Attach a
   `HookEventPolicy`/`NamespacePolicy` only if the agent needs that behavior — see
   `drivers/claude.py` for the fully-featured example and `drivers/kimi.py` for a minimal one.
2. Register it in `BUILTIN_DRIVERS` in `drivers/__init__.py`, and add the name to
   `Target` + `BUILTIN_TARGET_NAMES` in `models.py`.
3. Add a row to the capability table in `docs/architecture.md` and a case to
   `tests/test_drivers.py` (it's parametrized — usually a few lines).

> agentry maps **placement**, not format: it puts an authored file in the right directory,
> it doesn't translate a component between agent formats. The `Driver.transform` field is a
> reserved seam for that future capability (see the architecture doc).

**A new component type:**
1. Add it to `ComponentType` and to `LINK_TYPES` or `MERGE_TYPES` in `models.py`.
2. Add a `discovery.LAYOUT` entry (where it lives in a source repo).
3. Add a destination for it in each relevant driver's `TargetSpec`.
4. Update docs + tests.

**A new source kind:** add a `SourceType` value and a branch in `resolver.resolve`.

## Authoring a portable component repo (for component authors)

Write your skills/agents/commands once with the standard layout — agentry maps them into
whichever agents the consumer targets:

```
skills/<name>/        agents/<name>.md      commands/<name>.md
tools/<name>/         hooks/<name>.json     mcp/<name>.json
```

(Or self-describe a non-standard layout with an `agentry.yaml` descriptor — see the
architecture doc.) `hooks/*.json` and `mcp/*.json` are JSON **objects of named entries**
(see the merge contract in the architecture doc).

When a config fragment genuinely differs per agent, ship **per-harness variants** side by
side — `hooks/hooks.json` (canonical) plus `hooks/hooks-cursor.json`,
`hooks/hooks-codex.json`, etc. agentry routes each `-<harness>` variant only to its matching
target; the suffix-less file applies to every other target that supports the type.

## Tests & linting

- Add tests for any behavior change. The suite uses `tmp_path` fixtures and a tiny local
  git repo (`file://`) — no network required.
- Keep `agentry sync` **idempotent** and the **safety invariants** intact (never touch
  unmanaged files/links or hand-added config entries). There are tests guarding both;
  don't weaken them.
- Before opening a PR, run `uv run pytest`, `uvx ruff check .`, and
  `uvx ruff format --check .`. CI runs the same checks on Python 3.10–3.13.

## What CI does

Every push and PR runs **CI** (ruff lint/format + the pytest matrix). Pushes to `main`
redeploy the **docs site** to GitHub Pages (`docs.yml`), and **Release Drafter**
(`release-drafter.yml`) keeps a draft release note up to date from merged PR titles. You
don't need to do anything beyond opening a green PR.

There is **no PyPI channel** — the `agentry` name on PyPI belongs to an unrelated project.
GitHub Releases (binaries + native installers) is the sole distribution route.

**Releasing.** Bump the version and tag in one step:

```bash
python scripts/bump.py X.Y.Z   # edits pyproject + __init__ + CHANGELOG, commits, tags vX.Y.Z
git push --follow-tags
```

Pushing the `vX.Y.Z` tag fires **Release Binaries** (`release-binaries.yml`): it freezes the
`agentry` binary for Windows/macOS/Linux, builds the native installers (`.pkg`, `.exe`,
`.deb`, `.rpm`), signs every asset with cosign, attaches them plus `SHA256SUMS.txt` to the
GitHub Release, and pushes a refreshed formula to `OpenTechIL/homebrew-tap`. See
[packaging/README.md](packaging/README.md).

Note `scripts/bump.py` edits `pyproject.toml`, `src/agentry/__init__.py` and `CHANGELOG.md`
only — it does not touch the docs, so keep version references there generic.

## Commit & PR conventions

- Small, focused PRs. One behavior change per PR where possible.
- Use clear, imperative commit subjects (`add cursor mcp target`, `fix local symlink drift`).
- Update `docs/architecture.md` when you change behavior — it's the source of truth.

## Code of conduct

Be respectful and constructive. Assume good faith. Harassment of any kind is not tolerated.
This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md) — please read it.
