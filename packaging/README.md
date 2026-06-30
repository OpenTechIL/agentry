# Packaging & distribution

Beyond the `install.sh` / `install.ps1` one-liners and `uv tool install`, agentry ships
package-manager and devcontainer integrations. All consume the same GitHub release assets the
binary build produces (`release-binaries.yml`): a bare binary per target plus `SHA256SUMS.txt`.

```
agy-<version>-linux-x86_64
agy-<version>-macos-x86_64
agy-<version>-macos-arm64
agy-<version>-windows-x86_64.exe
SHA256SUMS.txt
```

## Homebrew — `homebrew/agy.rb`

A formula for a tap (e.g. `OpenTechIL/homebrew-tap`): then `brew install OpenTechIL/tap/agy`.
It selects the macOS/Linux binary by arch and verifies its `sha256`. **`version` and the
`sha256` values are release-specific**; the placeholders (64 zeros) are filled per release —
read them from `SHA256SUMS.txt` (the lines are `<sha256>  agy-<version>-<target>`). This can be
automated with a release step that bumps the formula in the tap repo.

## Scoop — `scoop/agy.json`

A Windows manifest: `scoop install agy` (from a bucket that includes this manifest). The static
`hash` for the pinned `version` is a placeholder to fill on release, but the `checkver` +
`autoupdate` blocks let `scoop update` self-bump from future releases automatically (the hash is
pulled from `SHA256SUMS.txt`). `#/agy.exe` renames the downloaded asset to `agy.exe`.

## Devcontainer Feature — `devcontainer/src/agentry/`

A [devcontainer Feature](https://containers.dev/implementors/features/) that installs `agy`
system-wide at image build, then — via `postCreateCommand` — runs `agy sync --frozen` once the
workspace is mounted **iff** it has a committed `.agentry.lock`. Zero-friction agent setup in
Codespaces/devcontainers. Reference it once published:

```jsonc
// .devcontainer/devcontainer.json
"features": {
  "ghcr.io/OpenTechIL/agentry/agentry:1": { "version": "latest" }
}
```

## Signing (not yet done — needs maintainer certificates)

The release binaries are currently **unsigned** (macOS prompts on first run via System Settings →
Privacy & Security; Windows can be blocked by AppLocker/WDAC on managed machines). Closing this
needs secrets only a maintainer can provide:

- **macOS** — an Apple Developer ID Application certificate + `codesign` + notarization (`notarytool`).
- **Windows** — an Authenticode code-signing certificate + `signtool` (or Azure Trusted Signing).

Wire these as encrypted CI secrets and add a signing step to `release-binaries.yml` after the
PyInstaller build, before checksums. Until then, these packaging files install the unsigned
binaries, identical to `install.sh`.
