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
