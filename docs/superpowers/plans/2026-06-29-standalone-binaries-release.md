# Standalone Binaries & GitHub Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship standalone `agy` executables for Windows/macOS/Linux on GitHub Releases, with one-line install scripts and a version-bump helper.

**Architecture:** PyInstaller freezes the existing Typer CLI into one-file binaries via a committed spec. A new `release-binaries.yml` workflow builds them on an OS matrix on `v*` tags and attaches them (plus a SHA256SUMS file) to a GitHub Release, leaving the existing PyPI `release.yml` untouched. `install.sh`/`install.ps1` download+verify the matching binary. `scripts/bump.py` keeps the two version files and CHANGELOG in sync and creates the tag.

**Tech Stack:** Python 3.10+, Typer/Rich, PyInstaller 6.x, `tomlkit` (already a dep), GitHub Actions, `softprops/action-gh-release`, `astral-sh/setup-uv`, POSIX sh + PowerShell.

## Global Constraints

- Canonical repo slug is **`OpenTechIL/agentry`** (the git remote) — use it in all new URLs, scripts, and workflows.
- Version lives in **two** files and must stay in sync: `pyproject.toml` `[project] version` and `src/agentry/__init__.py` `__version__` (currently both `0.1.0`).
- The CLI version command is **`agy version`** (a subcommand), printing `agentry <version>`. There is no `--version` flag — use `agy version` for smoke tests.
- Binary asset naming: `agy-<version>-<os>-<arch>[.exe]` where os ∈ {linux, macos, windows}, arch ∈ {x86_64, arm64}, `<version>` has **no** leading `v`.
- Built targets: linux-x86_64, macos-x86_64, macos-arm64, windows-x86_64. linux-arm64 and windows-arm64 are **not** built — scripts must fail clearly on those.
- Python floor is `>=3.10`; PyInstaller goes in a new `build` optional-dependency group, never a runtime dep.
- Tests run with `uv run --extra dev pytest`; lint/format with `uvx ruff check .` / `uvx ruff format --check .`.

---

### Task 1: PyInstaller packaging (entry + spec + build dep)

**Files:**
- Create: `packaging/entry.py`
- Create: `packaging/agy.spec`
- Modify: `pyproject.toml` (add `build` optional-dependency group)
- Modify: `.gitignore` (ignore PyInstaller `build/` and `dist/`)
- Test: `tests/test_packaging.py`

**Interfaces:**
- Produces: a one-file build at `dist/agy` (`dist/agy.exe` on Windows) that runs `agy version`. The spec name is `agy`. Consumed by Task 3 (workflow) and the install scripts.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_packaging.py
"""The PyInstaller entrypoint must import the real CLI app, not redefine it."""
from pathlib import Path


def test_entry_file_exists():
    assert Path("packaging/entry.py").is_file()


def test_entry_imports_cli_app():
    import importlib.util

    spec = importlib.util.spec_from_file_location("agy_entry", "packaging/entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # __name__ != "__main__", so app() is not invoked
    from agentry.cli import app

    assert module.app is app


def test_spec_file_exists():
    text = Path("packaging/agy.spec").read_text()
    assert "name='agy'" in text or 'name="agy"' in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_packaging.py -v`
Expected: FAIL — `packaging/entry.py` does not exist.

- [ ] **Step 3: Create the entry runner**

```python
# packaging/entry.py
"""PyInstaller entrypoint for the `agy` CLI.

Kept separate from the ``agy`` console-script in ``pyproject.toml`` so the
frozen binary has a stable, import-clean entry module.
"""

from agentry.cli import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Create the PyInstaller spec**

```python
# packaging/agy.spec — one-file build for the `agy` CLI.
# Run from the repo root: `uv run --extra build pyinstaller packaging/agy.spec`
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# ruamel.yaml ships C extensions + plugins PyInstaller misses; pydantic builds
# models dynamically. Collect both explicitly.
datas, binaries, hiddenimports = collect_all("ruamel.yaml")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_core")

entry = os.path.join(SPECPATH, "entry.py")
src = os.path.join(SPECPATH, "..", "src")

a = Analysis(
    [entry],
    pathex=[src],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

- [ ] **Step 5: Add the `build` optional-dependency group**

In `pyproject.toml`, under `[project.optional-dependencies]`, add the line (keep the others unchanged):

```toml
build = ["pyinstaller>=6.0"]
```

- [ ] **Step 6: Ignore PyInstaller output**

Append to `.gitignore`:

```gitignore
# PyInstaller
/build/
/dist/
```

- [ ] **Step 7: Run the unit tests to verify they pass**

Run: `uv run --extra dev pytest tests/test_packaging.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Verify a real local freeze works (functional gate)**

Run:
```bash
uv run --extra build pyinstaller packaging/agy.spec
./dist/agy version
```
Expected: build completes and the last command prints `agentry 0.1.0`. (On Windows the binary is `dist\agy.exe`.) If `ruamel.yaml`/`pydantic` import errors appear at runtime, they indicate a missing collect — re-check Step 4.

- [ ] **Step 9: Commit**

```bash
git add packaging/entry.py packaging/agy.spec pyproject.toml .gitignore tests/test_packaging.py
git commit -m "feat(packaging): PyInstaller one-file build for the agy CLI"
```

---

### Task 2: `scripts/bump.py` version-bump helper

**Files:**
- Create: `scripts/bump.py`
- Test: `tests/test_bump.py`

**Interfaces:**
- Produces (importable, pure functions — no I/O):
  - `parse_version(s: str) -> str` — returns `s` if it matches `^\d+\.\d+\.\d+$`, else raises `ValueError`.
  - `bump_pyproject(text: str, version: str) -> str` — returns pyproject text with `[project] version` set to `version`, comments/formatting preserved.
  - `bump_init(text: str, version: str) -> str` — returns `__init__.py` text with `__version__ = "<version>"`.
  - `bump_changelog(text: str, version: str, date: str) -> str` — turns the first `## [Unreleased]…` heading into a fresh `## [Unreleased]` followed by `## [<version>] — <date>`.
  - `main(argv: list[str]) -> int` — CLI: validates version, refuses a dirty tree, rewrites the three files, `git commit` + `git tag v<version>`, prints the push command.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bump.py
import pytest

from scripts.bump import bump_changelog, bump_init, bump_pyproject, parse_version


def test_parse_version_accepts_semver():
    assert parse_version("1.2.3") == "1.2.3"


@pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "1.2.3a", "x"])
def test_parse_version_rejects_non_semver(bad):
    with pytest.raises(ValueError):
        parse_version(bad)


def test_bump_pyproject_updates_version_and_preserves_rest():
    text = '[project]\nname = "agentry"\nversion = "0.1.0"  # keep me\n'
    out = bump_pyproject(text, "0.2.0")
    assert 'version = "0.2.0"' in out
    assert 'name = "agentry"' in out
    assert "0.1.0" not in out


def test_bump_init_updates_dunder_version():
    text = '"""doc."""\n\n__version__ = "0.1.0"\n'
    out = bump_init(text, "0.2.0")
    assert '__version__ = "0.2.0"' in out
    assert "0.1.0" not in out


def test_bump_changelog_inserts_dated_section_and_fresh_unreleased():
    text = (
        "# Changelog\n\n---\n\n"
        "## [Unreleased] — 2026-06-25\n\n"
        "### Added\n- a thing\n"
    )
    out = bump_changelog(text, "0.2.0", "2026-06-29")
    assert "## [Unreleased]\n" in out
    assert "## [0.2.0] — 2026-06-29" in out
    # the new Unreleased heading sits above the dated one
    assert out.index("## [Unreleased]") < out.index("## [0.2.0]")
    # the old entry content is retained under the dated section
    assert "- a thing" in out


def test_bump_changelog_is_idempotent_guarded():
    text = "## [Unreleased]\n\n## [0.2.0] — 2026-06-01\n"
    with pytest.raises(ValueError):
        bump_changelog(text, "0.2.0", "2026-06-29")  # version already released
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_bump.py -v`
Expected: FAIL — `scripts.bump` cannot be imported.

- [ ] **Step 3: Create `scripts/__init__.py` so the package is importable in tests**

```python
# scripts/__init__.py
```
(empty file)

- [ ] **Step 4: Implement `scripts/bump.py`**

```python
#!/usr/bin/env python3
"""Bump the agentry version across pyproject, __init__, and CHANGELOG, then tag.

Usage:  python scripts/bump.py X.Y.Z
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

import tomlkit

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "agentry" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def parse_version(s: str) -> str:
    if not _SEMVER.match(s):
        raise ValueError(f"not a X.Y.Z version: {s!r}")
    return s


def bump_pyproject(text: str, version: str) -> str:
    doc = tomlkit.parse(text)
    doc["project"]["version"] = version
    return tomlkit.dumps(doc)


def bump_init(text: str, version: str) -> str:
    new, n = re.subn(
        r'__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', text
    )
    if n != 1:
        raise ValueError("could not find a single __version__ assignment")
    return new


def bump_changelog(text: str, version: str, date: str) -> str:
    if f"## [{version}]" in text:
        raise ValueError(f"version {version} already present in CHANGELOG")
    pattern = re.compile(r"^## \[Unreleased\].*$", re.MULTILINE)
    replacement = f"## [Unreleased]\n\n## [{version}] — {date}"
    new, n = pattern.subn(replacement, text, count=1)
    if n != 1:
        raise ValueError("could not find an '## [Unreleased]' heading")
    return new


def _run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def _is_dirty() -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(out.stdout.strip())


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python scripts/bump.py X.Y.Z", file=sys.stderr)
        return 2
    try:
        version = parse_version(argv[0])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if _is_dirty():
        print("error: working tree is dirty; commit or stash first", file=sys.stderr)
        return 1

    today = datetime.date.today().isoformat()
    try:
        PYPROJECT.write_text(bump_pyproject(PYPROJECT.read_text(), version))
        INIT.write_text(bump_init(INIT.read_text(), version))
        CHANGELOG.write_text(bump_changelog(CHANGELOG.read_text(), version, today))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _run("git", "add", str(PYPROJECT), str(INIT), str(CHANGELOG))
    _run("git", "commit", "-m", f"chore(release): v{version}")
    _run("git", "tag", f"v{version}")
    print(f"\nTagged v{version}. Push with:\n  git push --follow-tags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/test_bump.py -v`
Expected: PASS (all cases).

- [ ] **Step 6: Lint/format the new code**

Run: `uvx ruff check scripts/ tests/test_bump.py && uvx ruff format scripts/ tests/test_bump.py`
Expected: no errors; formatting applied if needed.

- [ ] **Step 7: Commit**

```bash
git add scripts/__init__.py scripts/bump.py tests/test_bump.py
git commit -m "feat(release): add scripts/bump.py version-bump helper"
```

---

### Task 3: `release-binaries.yml` workflow

**Files:**
- Create: `.github/workflows/release-binaries.yml`

**Interfaces:**
- Consumes: `packaging/agy.spec` from Task 1; `agy version` output for `workflow_dispatch` version resolution.
- Produces: a GitHub Release (on `v*` tags) carrying `agy-<ver>-<target>[.exe]` for each target plus `SHA256SUMS.txt`.

- [ ] **Step 1: Create the workflow**

```yaml
# .github/workflows/release-binaries.yml
name: Release Binaries

# Builds standalone `agy` executables and attaches them to the GitHub Release
# for a v* tag. PyPI publishing lives separately in release.yml.
#
# workflow_dispatch builds + uploads artifacts for inspection but does NOT
# create a Release (dry run).
on:
  push:
    tags: ["v*"]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  build:
    name: Build ${{ matrix.target }}
    runs-on: ${{ matrix.runner }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - { runner: ubuntu-latest,  target: linux-x86_64,   ext: "" }
          - { runner: macos-13,       target: macos-x86_64,    ext: "" }
          - { runner: macos-14,       target: macos-arm64,     ext: "" }
          - { runner: windows-latest, target: windows-x86_64,  ext: ".exe" }
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - name: Build binary
        run: uv run --extra build pyinstaller packaging/agy.spec
      - name: Resolve version
        id: ver
        shell: bash
        run: |
          if [ "${GITHUB_REF_TYPE}" = "tag" ]; then
            echo "version=${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"
          else
            echo "version=$(./dist/agy${{ matrix.ext }} version | awk '{print $2}')" >> "$GITHUB_OUTPUT"
          fi
      - name: Rename artifact
        shell: bash
        run: mv "dist/agy${{ matrix.ext }}" "agy-${{ steps.ver.outputs.version }}-${{ matrix.target }}${{ matrix.ext }}"
      - name: Smoke test
        shell: bash
        run: ./agy-${{ steps.ver.outputs.version }}-${{ matrix.target }}${{ matrix.ext }} version
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: agy-${{ matrix.target }}
          path: agy-*

  release:
    name: Publish GitHub Release
    needs: build
    if: github.ref_type == 'tag'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Download all binaries
        uses: actions/download-artifact@v4
        with:
          path: artifacts
          merge-multiple: true
      - name: Generate checksums
        working-directory: artifacts
        run: |
          sha256sum agy-* > SHA256SUMS.txt
          cat SHA256SUMS.txt
      - name: Create / update Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            artifacts/agy-*
            artifacts/SHA256SUMS.txt
          generate_release_notes: true
```

- [ ] **Step 2: Validate the YAML parses**

Run: `uv run --extra dev python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release-binaries.yml'))" && echo OK`
Expected: prints `OK` (no parse error). If `yaml` is missing, use `python3 -c "import json,subprocess"` alternative — but `ruamel.yaml` is a project dep, so: `uv run python -c "from ruamel.yaml import YAML; YAML().load(open('.github/workflows/release-binaries.yml')); print('OK')"`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release-binaries.yml
git commit -m "ci(release): build standalone binaries and attach to GitHub Release"
```

---

### Task 4: Install scripts (`install.sh` + `install.ps1`)

**Files:**
- Create: `install.sh`
- Create: `install.ps1`

**Interfaces:**
- Consumes: release assets `agy-<ver>-<target>[.exe]` and `SHA256SUMS.txt` produced by Task 3.
- Produces: an installed `agy` on the host. Honors env overrides `AGENTRY_VERSION` (default `latest`) and `AGENTRY_INSTALL_DIR`.

- [ ] **Step 1: Create `install.sh`**

```sh
#!/bin/sh
# install.sh — download and install the `agy` binary from GitHub Releases.
#
#   curl -fsSL https://raw.githubusercontent.com/OpenTechIL/agentry/main/install.sh | sh
#
# Env: AGENTRY_VERSION (default: latest), AGENTRY_INSTALL_DIR (default: ~/.local/bin)
set -eu

REPO="OpenTechIL/agentry"
INSTALL_DIR="${AGENTRY_INSTALL_DIR:-$HOME/.local/bin}"

err() { echo "agentry-install: $*" >&2; exit 1; }

os=$(uname -s)
case "$os" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) err "unsupported OS: $os (try: uv tool install agentry)" ;;
esac

arch=$(uname -m)
case "$arch" in
  x86_64|amd64) arch=x86_64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) err "unsupported arch: $arch" ;;
esac

if [ "$os" = "linux" ] && [ "$arch" = "arm64" ]; then
  err "no prebuilt binary for linux-arm64 yet; install via 'uv tool install agentry'"
fi
target="${os}-${arch}"

version="${AGENTRY_VERSION:-latest}"
if [ "$version" = "latest" ]; then
  tag=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | grep '"tag_name"' | head -1 | cut -d'"' -f4)
  [ -n "$tag" ] || err "could not resolve the latest release"
else
  tag="v${version#v}"
fi

asset="agy-${tag#v}-${target}"
base="https://github.com/$REPO/releases/download/$tag"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "Downloading $asset ($tag)…"
curl -fsSL "$base/$asset" -o "$tmp/agy" || err "download failed: $base/$asset"
curl -fsSL "$base/SHA256SUMS.txt" -o "$tmp/SHA256SUMS.txt" || err "checksum download failed"

expected=$(grep " $asset\$" "$tmp/SHA256SUMS.txt" | awk '{print $1}')
[ -n "$expected" ] || err "no checksum entry for $asset"
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$tmp/agy" | awk '{print $1}')
else
  actual=$(shasum -a 256 "$tmp/agy" | awk '{print $1}')
fi
[ "$expected" = "$actual" ] || err "checksum mismatch (expected $expected, got $actual)"

mkdir -p "$INSTALL_DIR"
chmod +x "$tmp/agy"
mv "$tmp/agy" "$INSTALL_DIR/agy"
echo "Installed agy to $INSTALL_DIR/agy"

case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *) echo "Note: $INSTALL_DIR is not on your PATH. Add it, e.g.:"
     echo "  export PATH=\"$INSTALL_DIR:\$PATH\"" ;;
esac

"$INSTALL_DIR/agy" version
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x install.sh`

- [ ] **Step 3: Lint the shell script (if shellcheck is available)**

Run: `command -v shellcheck >/dev/null && shellcheck install.sh || echo "shellcheck not installed — skipping"`
Expected: no warnings, or a skip message.

- [ ] **Step 4: Create `install.ps1`**

```powershell
# install.ps1 — download and install the `agy` binary from GitHub Releases.
#
#   irm https://raw.githubusercontent.com/OpenTechIL/agentry/main/install.ps1 | iex
#
# Env: AGENTRY_VERSION (default: latest), AGENTRY_INSTALL_DIR
#Requires -Version 5
$ErrorActionPreference = 'Stop'

$Repo = 'OpenTechIL/agentry'
$InstallDir = if ($env:AGENTRY_INSTALL_DIR) { $env:AGENTRY_INSTALL_DIR } else { "$env:LOCALAPPDATA\Programs\agentry" }

$arch = switch ($env:PROCESSOR_ARCHITECTURE) {
  'AMD64' { 'x86_64' }
  'ARM64' { 'arm64' }
  default { throw "unsupported arch: $env:PROCESSOR_ARCHITECTURE" }
}
if ($arch -eq 'arm64') { throw "no prebuilt binary for windows-arm64 yet; use 'uv tool install agentry'" }
$target = "windows-$arch"

$version = if ($env:AGENTRY_VERSION) { $env:AGENTRY_VERSION } else { 'latest' }
if ($version -eq 'latest') {
  $tag = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name
} else {
  $tag = "v$($version.TrimStart('v'))"
}
$asset = "agy-$($tag.TrimStart('v'))-$target.exe"
$base  = "https://github.com/$Repo/releases/download/$tag"

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  Write-Host "Downloading $asset ($tag)…"
  Invoke-WebRequest "$base/$asset" -OutFile "$tmp\agy.exe"
  Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile "$tmp\SHA256SUMS.txt"

  $line = Select-String -Path "$tmp\SHA256SUMS.txt" -Pattern ([regex]::Escape($asset)) | Select-Object -First 1
  if (-not $line) { throw "no checksum entry for $asset" }
  $expected = $line.Line.Split(' ')[0].ToLower()
  $actual = (Get-FileHash "$tmp\agy.exe" -Algorithm SHA256).Hash.ToLower()
  if ($expected -ne $actual) { throw "checksum mismatch (expected $expected, got $actual)" }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  Move-Item -Force "$tmp\agy.exe" "$InstallDir\agy.exe"
  Write-Host "Installed agy to $InstallDir\agy.exe"

  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if ($userPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$InstallDir", 'User')
    Write-Host "Added $InstallDir to your user PATH — restart your shell to pick it up."
  }
  & "$InstallDir\agy.exe" version
} finally {
  Remove-Item -Recurse -Force $tmp
}
```

- [ ] **Step 5: Sanity-check the PowerShell parses (best effort, if pwsh is present)**

Run: `command -v pwsh >/dev/null && pwsh -NoProfile -Command "[void][System.Management.Automation.Language.Parser]::ParseFile('install.ps1',[ref]\$null,[ref]\$null); 'OK'" || echo "pwsh not installed — skipping"`
Expected: `OK` or the skip message.

- [ ] **Step 6: Commit**

```bash
git add install.sh install.ps1
git commit -m "feat(install): add curl|sh and irm|iex install scripts"
```

---

### Task 5: Documentation (README, CONTRIBUTING, CHANGELOG)

**Files:**
- Modify: `README.md:28-40` (the `## Install` section)
- Modify: `CONTRIBUTING.md:108-112` (the CI/Release note)
- Modify: `CHANGELOG.md` (the `[Unreleased]` section)

**Interfaces:**
- Consumes: the install one-liners and `bump.py` flow from Tasks 2–4.

- [ ] **Step 1: Rewrite the README `## Install` section**

Replace the current `## Install` section (lines 28–40) with:

```markdown
## Install

### Standalone binary (no Python required)

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/OpenTechIL/agentry/main/install.sh | sh
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/OpenTechIL/agentry/main/install.ps1 | iex
```

This downloads the right binary for your OS/arch from the [latest release](https://github.com/OpenTechIL/agentry/releases/latest), verifies its checksum, and installs `agy`. Pin a version with `AGENTRY_VERSION=0.2.0` or change the location with `AGENTRY_INSTALL_DIR`. (macOS binaries are unsigned — on first run, allow it via System Settings → Privacy & Security.)

**Tell your coding agent** to install it on a machine:

> Run: `curl -fsSL https://raw.githubusercontent.com/OpenTechIL/agentry/main/install.sh | sh` (or the PowerShell line above on Windows), then verify with `agy version`.

### With Python (uv / pipx)

Run straight from git, no install:

```bash
uvx --from git+https://github.com/OpenTechIL/agentry agy <command>
```

Or install into a project/venv:

```bash
uv pip install agentry        # then: agy <command>
```
```

- [ ] **Step 2: Update the CONTRIBUTING release note**

In `CONTRIBUTING.md`, find the paragraph beginning "Every push and PR runs **CI**" (around line 110) and append a "Releasing" note after it:

```markdown

**Releasing.** Bump the version and tag in one step:

```bash
python scripts/bump.py X.Y.Z   # edits pyproject + __init__ + CHANGELOG, commits, tags vX.Y.Z
git push --follow-tags
```

Pushing the `vX.Y.Z` tag fires two workflows: **Release** (`release.yml`, publishes to PyPI) and **Release Binaries** (`release-binaries.yml`, builds standalone executables for Windows/macOS/Linux and attaches them — with `SHA256SUMS.txt` — to the GitHub Release).
```

- [ ] **Step 3: Add a CHANGELOG `[Unreleased]` entry**

Under the `### Added` list in the `## [Unreleased]` section of `CHANGELOG.md`, add:

```markdown
- Standalone `agy` binaries for Windows, macOS, and Linux, built with PyInstaller
  and attached to each GitHub Release (`release-binaries.yml`), plus `install.sh` /
  `install.ps1` one-line installers that download and checksum-verify the binary.
- `scripts/bump.py X.Y.Z` to bump the version across `pyproject.toml`,
  `src/agentry/__init__.py`, and `CHANGELOG.md`, then commit and tag in one step.
```

- [ ] **Step 4: Verify docs build clean (mkdocs strict, as CI does)**

Run: `uv run --extra docs mkdocs build --strict 2>&1 | tail -5 || echo "mkdocs not configured for these files — ensure no broken links were introduced"`
Expected: build succeeds, or confirm the new README anchors don't break `mkdocs --strict` (the repo recently hardened this — see commit `bbf856d`).

- [ ] **Step 5: Commit**

```bash
git add README.md CONTRIBUTING.md CHANGELOG.md
git commit -m "docs: document binary install, install scripts, and release flow"
```

---

## Self-Review

**Spec coverage:**
- Goal 1 (binaries for 3 OSes) → Task 1 (freeze) + Task 3 (matrix build). ✔
- Goal 2 (publish on Release) → Task 3 `release` job. ✔
- Goal 3 (one-line install + agent command) → Task 4 + README in Task 5. ✔
- Goal 4 (version bump) → Task 2. ✔
- Goal 5 (README + CONTRIBUTING) → Task 5. ✔
- Non-goals (linux-arm64, signing, brew/scoop) → explicitly excluded; scripts fail clearly on unbuilt targets. ✔

**Placeholder scan:** No TBD/TODO; every code/YAML/shell step is complete. Conditional verification steps (shellcheck/pwsh/mkdocs) include explicit skip fallbacks rather than vague instructions.

**Type consistency:** `bump.py` function names/signatures in the Interfaces block match the test imports and implementation (`parse_version`, `bump_pyproject`, `bump_init`, `bump_changelog`, `main`). Asset name `agy-<ver>-<target>[.exe]` is identical across Task 3 (build/rename), Task 4 (both scripts' `$asset`/`asset`), and the SHA256SUMS grep. `agy version` (not `--version`) used in every smoke test. Repo slug `OpenTechIL/agentry` consistent throughout.
