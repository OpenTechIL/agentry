#!/usr/bin/env python3
"""Render the Homebrew formula for a release: fill version + per-target sha256.

Usage:  python scripts/render_homebrew.py <version> <sha256sums-path> <template-path>

Reads the placeholder formula (packaging/homebrew/agy.rb) and the release's
SHA256SUMS.txt, then prints the rendered formula to stdout with:
  * the `version "…"` line set to <version>, and
  * each `sha256 "0000…"` placeholder replaced by the real hash of the binary
    named in the preceding `url "…"` line.

The three placeholder shas in the template are identical, so a plain `sed`
can't tell them apart — we key each one off the `agy-<version>-<target>` asset
named in the url line just above it.
"""

from __future__ import annotations

import re
import sys

# url "…/agy-#{version}-<target>" — the template uses #{version} interpolation,
# so match the literal token here (the rendered URL is resolved by Homebrew).
_URL_TARGET = re.compile(r'url\s+".*agy-#\{version\}-([a-z0-9_-]+)"')
_VERSION_LINE = re.compile(r'^(\s*version\s+)"[^"]*"(.*)$')
_SHA_LINE = re.compile(r'^(\s*sha256\s+)"[^"]*"(.*)$')


def parse_sums(text: str) -> dict[str, str]:
    """Parse SHA256SUMS.txt lines (`<sha256>  <asset>`) into {asset: sha256}."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, asset = line.partition(" ")
        asset = asset.strip()
        if sha and asset:
            sums[asset] = sha
    return sums


def render(template: str, version: str, sums: dict[str, str]) -> str:
    out: list[str] = []
    pending_target: str | None = None
    for line in template.splitlines():
        m = _URL_TARGET.search(line)
        if m:
            pending_target = m.group(1)
            out.append(line)
            continue

        vm = _VERSION_LINE.match(line)
        if vm:
            out.append(f'{vm.group(1)}"{version}"{vm.group(2)}')
            continue

        sm = _SHA_LINE.match(line)
        if sm and pending_target is not None:
            asset = f"agy-{version}-{pending_target}"
            sha = sums.get(asset)
            if not sha:
                raise SystemExit(
                    f"error: no sha256 for {asset} in SHA256SUMS.txt "
                    f"(have: {', '.join(sorted(sums)) or 'nothing'})"
                )
            out.append(f'{sm.group(1)}"{sha}"{sm.group(2)}')
            pending_target = None
            continue

        out.append(line)

    return "\n".join(out) + ("\n" if template.endswith("\n") else "")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: python scripts/render_homebrew.py <version> <sha256sums-path> <template-path>",
            file=sys.stderr,
        )
        return 2
    version, sums_path, template_path = argv
    with open(sums_path, encoding="utf-8") as f:
        sums = parse_sums(f.read())
    with open(template_path, encoding="utf-8") as f:
        template = f.read()
    sys.stdout.write(render(template, version, sums))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
