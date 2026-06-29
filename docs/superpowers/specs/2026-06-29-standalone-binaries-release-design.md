# Standalone Binaries & GitHub Release Pipeline — Design

**Date:** 2026-06-29
**Status:** Approved (design)
**Component:** packaging / release automation

## Problem

`agentry` ships today only as a Python package on PyPI (`release.yml` builds a
wheel + sdist on `v*` tags and publishes via Trusted Publishing). Installing it
therefore requires a Python toolchain (`uv tool install agentry` / `pipx`).

We want users — and AI coding agents acting on a machine — to install `agy`
with a single command that needs **no pre-existing Python**, by downloading a
self-contained binary from a GitHub Release.

## Goals

1. Produce standalone `agy` executables for Windows, macOS, and Linux.
2. Publish those binaries (plus checksums) on a GitHub Release automatically.
3. Provide one-line install commands (shell + PowerShell) that fetch and install
   the right binary, suitable to hand to a coding agent.
4. Provide a repeatable version-bump step.
5. Update README (install instructions) and CONTRIBUTING (release process).

## Non-Goals (YAGNI — noted as possible future follow-ups)

- Linux `aarch64` binaries.
- macOS code-signing / notarization (binaries will be unsigned; install docs
  note the Gatekeeper first-run step).
- Homebrew tap / Scoop manifest / `.deb`/`.rpm`/MSI packaging.
- Auto-update mechanism inside `agy`.

## Components

### 1. PyInstaller freeze

- **`packaging/entry.py`** — thin runner so PyInstaller has a clean script
  entrypoint, decoupled from the `agy` console-script defined in
  `pyproject.toml`:

  ```python
  from agentry.cli import app

  if __name__ == "__main__":
      app()
  ```

- **`packaging/agy.spec`** — PyInstaller spec capturing a one-file build named
  `agy`, with the dependencies PyInstaller under-detects made explicit:
  - `--collect-all ruamel.yaml` (C-ext + plugin discovery)
  - `--collect-submodules pydantic` / `pydantic_core`
  - typer / rich / tomlkit are import-clean and need no special handling, but
    will be confirmed by the CI smoke test.

  Using a committed spec keeps local and CI builds identical.

- **PyInstaller is a build-only dependency.** Add a `build` optional-dependency
  group to `pyproject.toml` (`pyinstaller>=6.0`) rather than a runtime dep.

### 2. `.github/workflows/release-binaries.yml`

Separate from `release.yml` so PyPI publishing is untouched.

- **Triggers:** `push` on tags matching `v*`; `workflow_dispatch` (manual
  dry-run that builds + uploads artifacts but does **not** create a Release).
- **`build` job — matrix:**

  | runner         | os     | arch    | artifact name                       |
  |----------------|--------|---------|-------------------------------------|
  | ubuntu-latest  | linux  | x86_64  | `agy-<ver>-linux-x86_64`            |
  | macos-13       | macos  | x86_64  | `agy-<ver>-macos-x86_64`            |
  | macos-14       | macos  | arm64   | `agy-<ver>-macos-arm64`             |
  | windows-latest | windows| x86_64  | `agy-<ver>-windows-x86_64.exe`      |

  Each matrix leg: checkout → install uv → `uv sync --extra build` →
  `uv run pyinstaller packaging/agy.spec` → rename output to the
  versioned artifact name → **smoke test** (`./agy-<ver>-... version` must print
  the version) → `actions/upload-artifact`.

  `<ver>` is derived from the tag (`${GITHUB_REF_NAME#v}`); for
  `workflow_dispatch` it falls back to `agy`'s own reported version.

- **`release` job** (`needs: build`, only on tag pushes):
  - `permissions: contents: write`.
  - Download all artifacts into one directory.
  - Generate `SHA256SUMS.txt` over the binaries.
  - `softprops/action-gh-release@v2` creates/updates the Release for the tag and
    uploads every binary + `SHA256SUMS.txt`. Release notes come from the
    existing release-drafter draft / CHANGELOG section.

### 3. Install scripts

Both resolve the release asset for the host, verify the SHA256 against
`SHA256SUMS.txt`, and install the binary.

- **`install.sh`** (macOS/Linux):
  - Detect OS via `uname -s` (`Darwin`→macos, `Linux`→linux) and arch via
    `uname -m` (`x86_64`→x86_64, `arm64`/`aarch64`→arm64).
  - Version: latest release via the GitHub API `releases/latest`, overridable
    with `AGENTRY_VERSION`.
  - Download binary + `SHA256SUMS.txt`, verify checksum, `chmod +x`, install to
    `${AGENTRY_INSTALL_DIR:-$HOME/.local/bin}/agy`.
  - Print a PATH hint if the install dir isn't on `PATH`. Fail clearly on an
    unsupported os/arch combination (e.g. linux-arm64, which we don't build).
  - Repo-hosted, run via `curl -fsSL .../install.sh | sh`.

- **`install.ps1`** (Windows):
  - Detect arch from `$env:PROCESSOR_ARCHITECTURE`.
  - Same version-resolution + checksum-verify logic.
  - Install to `$env:LOCALAPPDATA\Programs\agentry\agy.exe`; add that dir to the
    user `PATH` if absent.
  - Run via `irm .../install.ps1 | iex`.

### 4. `scripts/bump.py`

- Usage: `python scripts/bump.py X.Y.Z` (validates semver shape).
- Updates the version in **both** places it lives today:
  - `pyproject.toml` `[project] version` (via `tomlkit`, preserving comments).
  - `src/agentry/__init__.py` `__version__`.
- CHANGELOG: rewrite the `## [Unreleased] — <date>` heading into
  `## [X.Y.Z] — <today>` and insert a fresh empty `## [Unreleased]` section
  above it.
- Create a `chore(release): vX.Y.Z` commit and a `vX.Y.Z` tag, then print the
  `git push --follow-tags` command (the script does not push).
- Guard: refuse to run on a dirty working tree.

## Data Flow

```
scripts/bump.py 0.2.0
  └─ edits pyproject.toml + __init__.py + CHANGELOG.md, commits, tags v0.2.0
        │  git push --follow-tags
        ▼
tag v0.2.0 pushed ──► release.yml          ──► PyPI (wheel + sdist)   [unchanged]
                  └─► release-binaries.yml ──► build matrix (4 OS/arch)
                                                  └─► GitHub Release
                                                        agy-0.2.0-*  + SHA256SUMS.txt
                                                              ▲
install.sh / install.ps1 ──── download + verify ─────────────┘
```

## Error Handling

- **Install scripts:** explicit failure on unsupported os/arch, on a 404 release
  asset, and on checksum mismatch (abort, do not install a partial/corrupt
  binary). Non-zero exit + human-readable message in all cases.
- **CI smoke test:** if the frozen binary can't run `agy version`, the matrix leg
  fails before any Release is touched (the `release` job `needs: build`).
- **bump.py:** abort on dirty tree, invalid version string, or if the target
  version already appears in the CHANGELOG.

## Testing

- `tests/test_bump.py` — unit-test the file-rewrite helpers in `bump.py`
  (pyproject edit, `__init__` edit, CHANGELOG transform) against fixtures in a
  temp dir; assert idempotency guards reject a dirty/invalid input. Git
  commit/tag side effects are exercised against a temp repo or factored behind a
  thin function and skipped if `git` unavailable.
- **Install scripts:** lint only (`shellcheck` for `install.sh` if available);
  end-to-end download is validated manually against the first real release since
  it depends on published assets.
- **CI smoke test** (above) is the functional gate for the binaries themselves.

## Documentation

- **README.md** — new "Install" subsection ordered:
  1. Binary one-liners (`curl … | sh`, `irm … | iex`).
  2. "Tell your coding agent" — a single copy-paste command an agent can run.
  3. Existing `uv tool install agentry` / `pipx` path for Python users.
- **CONTRIBUTING.md** — short "Releasing" note: `bump.py` → push tag → the two
  workflows fire.
- **CHANGELOG.md** — an `[Unreleased]` entry describing the new binaries +
  install scripts.

## Single-Source-of-Version Note

Version currently lives in two files (`pyproject.toml`, `src/agentry/__init__.py`).
`bump.py` keeps them in sync. Collapsing to a single source (e.g. hatchling
dynamic version reading `__version__`) is out of scope here but noted as a
possible later cleanup.
