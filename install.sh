#!/bin/sh
# install.sh — download and install the `agentry` binary from GitHub Releases.
#
# Installs the one-folder build (the `agentry` executable plus its `_internal/` libraries)
# into AGENTRY_LIB_DIR, and links `agentry` plus the short aliases `agy` and `agyx` into
# AGENTRY_INSTALL_DIR. `agy` is also the command for Google's Antigravity CLI; `agyx` is
# the short name that cannot collide. Releases without a folder archive (before 0.3.5)
# install the single-file binary instead.
#
#   curl -fsSL https://raw.githubusercontent.com/OpenTechIL/agentry/main/install.sh | sh
#
# Env: AGENTRY_VERSION (default: latest), AGENTRY_INSTALL_DIR (default: ~/.local/bin),
#      AGENTRY_LIB_DIR (default: <AGENTRY_INSTALL_DIR>/../lib/agentry, so ~/.local/lib/agentry)
set -eu

REPO="OpenTechIL/agentry"
INSTALL_DIR="${AGENTRY_INSTALL_DIR:-$HOME/.local/bin}"
LIB_DIR="${AGENTRY_LIB_DIR:-$(dirname "$INSTALL_DIR")/lib/agentry}"

err() { echo "agentry-install: $*" >&2; exit 1; }

# LIB_DIR is only ever deleted or replaced when it's an earlier agentry folder install
# (it has the _internal/ libraries), never when it's something else the user owns.
is_agentry_lib() { [ -x "$LIB_DIR/agentry" ] && [ -d "$LIB_DIR/_internal" ]; }

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

# Prefer the one-folder archive (0.3.5 and later). Otherwise use the single-file binary.
# Release assets were named agy-<version>-<target> before 0.1.4: that name is tried last,
# so this script can still install releases older than 0.1.4, which only published agy-*
# assets. (Copies of this script older than 0.1.4 can't install current releases: those
# no longer publish agy-* assets.)
asset=""
for candidate in "agentry-${version_no_v}-${target}.tar.gz" "agentry-${version_no_v}-${target}" "agy-${version_no_v}-${target}"; do
  if grep -q "  $candidate\$" "$tmp/SHA256SUMS.txt"; then
    asset="$candidate"
    break
  fi
done
[ -n "$asset" ] || err "no asset for $target in release $tag"

echo "Downloading $asset ($tag)…"
curl -fsSL "$base/$asset" -o "$tmp/download" || err "download failed: $base/$asset"

expected=$(grep "  $asset\$" "$tmp/SHA256SUMS.txt" | awk '{print $1}')
[ -n "$expected" ] || err "no checksum entry for $asset"
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$tmp/download" | awk '{print $1}')
else
  actual=$(shasum -a 256 "$tmp/download" | awk '{print $1}')
fi
[ "$expected" = "$actual" ] || err "checksum mismatch (expected $expected, got $actual)"

mkdir -p "$INSTALL_DIR"
single="$tmp/download"
case "$asset" in
  *.tar.gz)
    tar -xzf "$tmp/download" -C "$tmp" || err "could not unpack $asset"
    # Linux tarballs before 0.3.5 held the single-file binary itself, not a folder.
    if [ -f "$tmp/agentry" ]; then
      single="$tmp/agentry"
      asset="single-file"
    fi
    ;;
esac
case "$asset" in
  *.tar.gz)
    # The archive holds one agentry/ folder. It was unpacked into the temp dir; move it
    # into place next to LIB_DIR, then swap it in, so a failed step never leaves a
    # half-installed folder behind.
    [ -x "$tmp/agentry/agentry" ] || err "$asset has no agentry/agentry executable"
    if [ -e "$LIB_DIR" ] && ! is_agentry_lib; then
      err "$LIB_DIR exists and isn't an agentry install; set AGENTRY_LIB_DIR to another folder"
    fi
    mkdir -p "$(dirname "$LIB_DIR")"
    rm -rf "$LIB_DIR.new"
    mv "$tmp/agentry" "$LIB_DIR.new"
    rm -rf "$LIB_DIR"
    mv "$LIB_DIR.new" "$LIB_DIR"
    # A link, not a copy: the executable finds _internal/ next to its real path. An older
    # install left a real binary here, and rm -f replaces it.
    rm -f "$INSTALL_DIR/agentry"
    ln -s "$LIB_DIR/agentry" "$INSTALL_DIR/agentry"
    ;;
  *)
    chmod +x "$single"
    rm -f "$INSTALL_DIR/agentry"
    mv "$single" "$INSTALL_DIR/agentry"
    # A single-file install replaces a folder install's link; drop the unused folder.
    if is_agentry_lib; then rm -rf "$LIB_DIR"; fi
    ;;
esac
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
