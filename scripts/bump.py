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
    new, n = re.subn(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', text)
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
    # Annotated tag: `git push --follow-tags` only pushes annotated tags, so a
    # lightweight tag would silently stay local and never trigger the release.
    _run("git", "tag", "-a", f"v{version}", "-m", f"v{version}")
    print(f"\nTagged v{version}. Push with:\n  git push --follow-tags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
