# Packaging & distribution

Beyond the `install.sh` / `install.ps1` one-liners and `uv tool install`, agentry ships
package-manager and devcontainer integrations. All consume the GitHub release assets the
binary build produces (`release-binaries.yml`): a bare binary per target, native installers,
`SHA256SUMS.txt`, and a `.cosign.bundle` Sigstore signature per asset.

```
agy-<version>-linux-x86_64                 raw binary
agy-<version>-macos-x86_64                 raw binary
agy-<version>-macos-arm64                  raw binary
agy-<version>-macos-x86_64.pkg             macOS installer (per-user)
agy-<version>-macos-arm64.pkg              macOS installer (per-user)
agy-<version>-windows-x86_64.exe           raw binary
agy-<version>-windows-x86_64-setup.exe     Inno Setup installer
agy_<version>_amd64.deb                    Debian/Ubuntu package
agy-<version>.x86_64.rpm                   Fedora/RHEL package
agy-<version>-linux-x86_64.tar.gz          tarball
SHA256SUMS.txt                             checksums for all of the above
<asset>.cosign.bundle                      keyless Sigstore signature per asset
```

## Windows installer — `windows/agy.iss`

An [Inno Setup](https://jrsoftware.org/isinfo.php) script compiled by `ISCC.exe` in CI
(`iscc /DMyAppVersion=<version> packaging\windows\agy.iss`). It produces a per-user installer
(`agy-<version>-windows-x86_64-setup.exe`, no admin required) that drops `agy.exe` under
`%LOCALAPPDATA%\Programs\agentry`, adds it to the user PATH, and registers an uninstaller —
same install location as `install.ps1`.

## Linux packages — `nfpm.yaml`

An [nfpm](https://nfpm.goreleaser.com/) config that packages the frozen binary into a `.deb`
and `.rpm` installing `/usr/bin/agy`. The version is injected via the `VERSION` env var at
build time (`VERSION=<version> nfpm package -f packaging/nfpm.yaml -p deb -t .`).

```sh
sudo apt install ./agy_<version>_amd64.deb      # Debian/Ubuntu
sudo dnf install ./agy-<version>.x86_64.rpm      # Fedora/RHEL
```

## macOS installer — `macos/distribution.xml`

A [`productbuild`](https://developer.apple.com/library/archive/documentation/DeveloperTools/Reference/DistributionDefinitionRef/Introduction/Introduction.html)
distribution compiled in CI. The workflow wraps the frozen binary in a component pkg with
`pkgbuild`, then runs `productbuild` against this distribution to produce a per-user
installer (`agy-<version>-macos-{x86_64,arm64}.pkg`, no admin required). `enable_currentUserHome`
lays the payload down relative to the user's home, so `agy` installs to `~/.local/bin/agy` —
same location as `install.sh`. The `macos/scripts/postinstall` script adds `~/.local/bin` to
PATH in `~/.zprofile` / `~/.bash_profile` (idempotently), since a GUI installer can't print a
terminal note. The version is injected via a `${VERSION}` placeholder at build time.

```sh
installer -pkg agy-<version>-macos-arm64.pkg -target CurrentUserHomeDirectory   # or double-click
```

Like the other assets it is **not** OS code-signed or notarized (see the signing note below),
so the macOS Gatekeeper first-run prompt still applies.

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

## Signing — cosign keyless (Sigstore)

Every release asset is signed with [cosign](https://docs.sigstore.dev/) **keyless** in
`release-binaries.yml`, using the workflow's GitHub OIDC identity — no certificates, private
keys, or CI secrets. Each asset gets a self-contained `<asset>.cosign.bundle` (signature +
Fulcio certificate + Rekor transparency-log entry) attached to the release. Verify a download:

```sh
cosign verify-blob \
  --bundle agy-<version>-linux-x86_64.cosign.bundle \
  --certificate-identity-regexp '^https://github.com/OpenTechIL/agentry/\.github/workflows/release-binaries\.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  agy-<version>-linux-x86_64
```

The same works against `SHA256SUMS.txt.cosign.bundle`, or against any installer/package
(`.exe` / `.deb` / `.rpm`) by pointing `--bundle` at its matching `.cosign.bundle`.

**Scope of what this proves and doesn't:** cosign gives cryptographic, publicly-auditable
provenance (this artifact was built by *this* workflow in *this* repo). It is **not** OS-level
code signing — it does **not** remove the Windows SmartScreen "unknown publisher" prompt or the
macOS Gatekeeper first-run prompt. Removing those still needs an Authenticode certificate
(Windows) and an Apple Developer ID + notarization (macOS); wire those as CI secrets and add a
platform signing step after the PyInstaller build if/when those certificates are available.
