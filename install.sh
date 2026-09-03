#!/bin/sh
# install.sh — download and install the `agentry` binary from GitHub Releases.
#
# Installs `agentry` plus the short aliases `agy` and `agyx` as symlinks. `agy` is also
# the command for Google's Antigravity CLI; `agyx` is the short name that cannot collide.
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
  *) err "unsupported OS: $os (try: uv tool install git+https://github.com/OpenTechIL/agentry)" ;;
esac

arch=$(uname -m)
case "$arch" in
  x86_64|amd64) arch=x86_64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) err "unsupported arch: $arch" ;;
esac

if [ "$os" = "linux" ] && [ "$arch" = "arm64" ]; then
  err "no prebuilt binary for linux-arm64 yet; install via 'uv tool install git+https://github.com/OpenTechIL/agentry'"
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

version_no_v="${tag#v}"
base="https://github.com/$REPO/releases/download/$tag"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

curl -fsSL "$base/SHA256SUMS.txt" -o "$tmp/SHA256SUMS.txt" || err "checksum download failed"

# Release assets were named agy-<version>-<target> before 0.1.4. Prefer the current name
# and fall back, so an older copy of this script keeps working against a new release and
# a new copy keeps working against an old one.
asset=""
for candidate in "agentry-${version_no_v}-${target}" "agy-${version_no_v}-${target}"; do
  if grep -q "  $candidate\$" "$tmp/SHA256SUMS.txt"; then
    asset="$candidate"
    break
  fi
done
[ -n "$asset" ] || err "no asset for $target in release $tag"

echo "Downloading $asset ($tag)…"
curl -fsSL "$base/$asset" -o "$tmp/agentry" || err "download failed: $base/$asset"

expected=$(grep "  $asset\$" "$tmp/SHA256SUMS.txt" | awk '{print $1}')
[ -n "$expected" ] || err "no checksum entry for $asset"
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$tmp/agentry" | awk '{print $1}')
else
  actual=$(shasum -a 256 "$tmp/agentry" | awk '{print $1}')
fi
[ "$expected" = "$actual" ] || err "checksum mismatch (expected $expected, got $actual)"

mkdir -p "$INSTALL_DIR"
chmod +x "$tmp/agentry"
mv "$tmp/agentry" "$INSTALL_DIR/agentry"
# Short aliases. Replaced unconditionally: an older install left a real binary at
# $INSTALL_DIR/agy, and it must become a link to the new one rather than go stale.
for alias_name in agy agyx; do
  rm -f "$INSTALL_DIR/$alias_name"
  ln -s "agentry" "$INSTALL_DIR/$alias_name"
done
echo "Installed agentry to $INSTALL_DIR/agentry (aliases: agy, agyx)"

case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *) echo "Note: $INSTALL_DIR is not on your PATH. Add it, e.g.:"
     echo "  export PATH=\"$INSTALL_DIR:\$PATH\"" ;;
esac

"$INSTALL_DIR/agentry" version || true
