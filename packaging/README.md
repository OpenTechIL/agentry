# Packaging & distribution

Beyond the `install.sh` / `install.ps1` one-liners and `uv tool install`, agentry ships
package-manager and devcontainer integrations. All consume the GitHub release assets the
binary build produces (`release-binaries.yml`): a one-folder archive and a bare binary per
target, native installers, `SHA256SUMS.txt`, and a `.cosign.bundle` Sigstore signature per asset.

```
agentry-<version>-linux-x86_64.tar.gz          one-folder build (agentry/ folder)
agentry-<version>-macos-x86_64.tar.gz          one-folder build (agentry/ folder)
agentry-<version>-macos-arm64.tar.gz           one-folder build (agentry/ folder)
agentry-<version>-windows-x86_64.zip           one-folder build (agentry\ folder)
agentry-<version>-linux-x86_64                 single-file binary
agentry-<version>-macos-x86_64                 single-file binary
agentry-<version>-macos-arm64                  single-file binary
agentry-<version>-windows-x86_64.exe           single-file binary
agentry-<version>-macos-x86_64.pkg             macOS installer (per-user)
agentry-<version>-macos-arm64.pkg              macOS installer (per-user)
agentry-<version>-windows-x86_64-setup.exe     Inno Setup installer
agentry_<version>_amd64.deb                    Debian/Ubuntu package
agentry-<version>.x86_64.rpm                   Fedora/RHEL package
SHA256SUMS.txt                             checksums for all of the above
<asset>.cosign.bundle                      keyless Sigstore signature per asset
```

## One-folder vs one-file builds — `agentry.spec`

PyInstaller builds agentry twice per target. The **one-folder** build (`dist/agentry/`: the
`agentry` executable plus `_internal/`) is what every installer and archive ships. The
**one-file** build (`AGENTRY_ONEFILE=1`) is only published as the bare
`agentry-<version>-<target>` download.

The reason is startup time. A one-file build unpacks itself into a new temp folder on every
run. On macOS, the unsigned libraries it unpacks are security-scanned each time, because the
path is new every time, so every command took about 5s. The one-folder build is scanned once
and then starts in about 0.2s. The executable finds `_internal/` through symlinks, so every
channel installs the folder somewhere fixed and links the command to it:

| Channel | Folder | Command |
|---|---|---|
| Homebrew | `<keg>/libexec/` | `bin/agentry` → `libexec/agentry` |
| `install.sh`, macOS `.pkg` | `~/.local/lib/agentry/` (`AGENTRY_LIB_DIR`) | `~/.local/bin/agentry` → link |
| `.deb` / `.rpm` | `/usr/lib/agentry/` | `/usr/bin/agentry` → link |
| `install.ps1`, Inno Setup, Scoop | the install dir itself (`agentry.exe` + `_internal\`) | `agentry.exe` on `PATH` |

Every channel replaces `_internal/` whole on upgrade, so no library from an older version stays
behind.

## Windows installer — `windows/agentry.iss`

An [Inno Setup](https://jrsoftware.org/isinfo.php) script compiled by `ISCC.exe` in CI
(`iscc /DMyAppVersion=<version> packaging\windows\agentry.iss`). It produces a per-user installer
(`agentry-<version>-windows-x86_64-setup.exe`, no admin required) that drops `agentry.exe` and
`_internal\` under `%LOCALAPPDATA%\Programs\agentry`, adds it to the user PATH, and registers an uninstaller —
same install location as `install.ps1`.

## Linux packages — `nfpm.yaml`

An [nfpm](https://nfpm.goreleaser.com/) config that packages the one-folder build into a `.deb`
and `.rpm` installing `/usr/lib/agentry/`, with `/usr/bin/agentry` (and the aliases) linked to it.
CI installs the `.deb` on the runner and runs `agentry version` / `agyx` before uploading it. The version is injected via the `VERSION` env var at
build time (`VERSION=<version> nfpm package -f packaging/nfpm.yaml -p deb -t .`).

```sh
sudo apt install ./agentry_<version>_amd64.deb      # Debian/Ubuntu
sudo dnf install ./agentry-<version>.x86_64.rpm      # Fedora/RHEL
```

## macOS installer — `macos/distribution.xml`

A [`productbuild`](https://developer.apple.com/library/archive/documentation/DeveloperTools/Reference/DistributionDefinitionRef/Introduction/Introduction.html)
distribution compiled in CI. The workflow wraps the one-folder build in a component pkg with
`pkgbuild`, then runs `productbuild` against this distribution to produce a per-user
installer (`agentry-<version>-macos-{x86_64,arm64}.pkg`, no admin required). `enable_currentUserHome`
lays the payload down relative to the user's home: the folder installs to
`~/.local/lib/agentry/`, and `~/.local/bin/agentry` links to it (with `agy`/`agyx` symlinks
beside it) — same locations as `install.sh`. The `macos/scripts/preinstall` script first removes
an earlier folder install (only when it has `_internal/`) and a real binary left at
`~/.local/bin/agentry` by older installs. The `macos/scripts/postinstall` script adds `~/.local/bin` to
PATH in `~/.zprofile` / `~/.bash_profile` (idempotently), since a GUI installer can't print a
terminal note. The version is injected via a `${VERSION}` placeholder at build time.

```sh
installer -pkg agentry-<version>-macos-arm64.pkg -target CurrentUserHomeDirectory   # or double-click
```

Like the other assets it is **not** OS code-signed or notarized (see the signing note below),
so the macOS Gatekeeper first-run prompt still applies.

## Homebrew — `homebrew/agentry.rb`

A formula for a tap (`OpenTechIL/homebrew-tap`): then `brew install OpenTechIL/tap/agentry`.
It selects the macOS/Linux one-folder `.tar.gz` by arch, verifies its `sha256`, installs the
folder into `libexec`, and links `bin/agentry` (and the aliases) to it. **`version` and the
`sha256` values are release-specific**; the in-repo `agentry.rb` keeps 64-zero placeholders so the
structure stays reviewable, and they're filled per release from `SHA256SUMS.txt` (the lines are
`<sha256>  agentry-<version>-<target>.tar.gz`).

This is **automated**: the `homebrew` job in `release-binaries.yml` runs on every `v*` tag,
renders the formula with `scripts/render_homebrew.py <version> SHA256SUMS.txt homebrew/agentry.rb`
(the render keys each sha off the `agentry-<version>-<target>.tar.gz` asset named in the url line above it),
and pushes the result to `Formula/agentry.rb` in the tap repo. Two one-time prerequisites:

1. Create the tap repo **`OpenTechIL/homebrew-tap`** (formulas live in `Formula/`).
2. Add a repo secret **`HOMEBREW_TAP_TOKEN`** to `OpenTechIL/agentry` — a fine-grained PAT (or
   deploy key) with `contents: write` on `homebrew-tap`. The default `GITHUB_TOKEN` can't push
   to a different repo. Until the secret exists the job logs a skip and the release still succeeds.

## Scoop — `scoop/agentry.json`

A Windows manifest: `scoop install agentry` (from a bucket that includes this manifest). The static
`hash` for the pinned `version` is a placeholder to fill on release, but the `checkver` +
`autoupdate` blocks let `scoop update` self-bump from future releases automatically (the hash is
pulled from `SHA256SUMS.txt`). It downloads the one-folder `.zip` and unpacks its `agentry\`
folder (`extract_dir`), so `agentry.exe` sits next to `_internal\`.

## Devcontainer Feature — `devcontainer/src/agentry/`

A [devcontainer Feature](https://containers.dev/implementors/features/) that installs `agentry`
system-wide at image build, then — via `postCreateCommand` — runs `agentry sync --frozen` once the
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
  --bundle agentry-<version>-linux-x86_64.cosign.bundle \
  --certificate-identity-regexp '^https://github.com/OpenTechIL/agentry/\.github/workflows/release-binaries\.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  agentry-<version>-linux-x86_64
```

The same works against `SHA256SUMS.txt.cosign.bundle`, or against any installer/package
(`.exe` / `.deb` / `.rpm`) by pointing `--bundle` at its matching `.cosign.bundle`.

Releases after 0.3.3 are signed in the standard Sigstore bundle format; 0.3.3 and earlier used
cosign's older bundle format. The command above verifies both formats with cosign 2.6.5 and
3.x, with no extra flags. If an older cosign can't read a bundle, upgrade cosign.

**Scope of what this proves and doesn't:** cosign gives cryptographic, publicly-auditable
provenance (this artifact was built by *this* workflow in *this* repo). It is **not** OS-level
code signing — it does **not** remove the Windows SmartScreen "unknown publisher" prompt or the
macOS Gatekeeper first-run prompt. Removing those still needs an Authenticode certificate
(Windows) and an Apple Developer ID + notarization (macOS); wire those as CI secrets and add a
platform signing step after the PyInstaller build if/when those certificates are available.
