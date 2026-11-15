# Homebrew formula for agentry.
#
# Intended for a tap (e.g. `OpenTechIL/homebrew-tap`): `brew install OpenTechIL/tap/agentry`.
# `version` and the per-asset `sha256` values are release-specific — release automation
# fills them from SHA256SUMS.txt (see packaging/README.md). The 64-zero shas below are
# placeholders so the structure is reviewable before the first tagged release.
class Agentry < Formula
  desc "Dependency manager for AI coding agents"
  homepage "https://github.com/OpenTechIL/agentry"
  version "0.1.0"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/OpenTechIL/agentry/releases/download/v#{version}/agentry-#{version}-macos-arm64.tar.gz"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
    on_intel do
      url "https://github.com/OpenTechIL/agentry/releases/download/v#{version}/agentry-#{version}-macos-x86_64.tar.gz"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/OpenTechIL/agentry/releases/download/v#{version}/agentry-#{version}-linux-x86_64.tar.gz"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
    # linux-arm64 has no prebuilt binary yet (see install.sh); use `uv tool install`.
  end

  def install
    # The release asset is a one-folder build: an agentry/ folder holding the `agentry`
    # executable and its `_internal/` libraries. Homebrew unpacks it and enters agentry/,
    # so the folder's contents go to libexec and bin/ only gets symlinks. The executable
    # finds `_internal/` through the symlink. A one-folder build starts in ~0.2s, where
    # the one-file build unpacked itself on every run (~5s on macOS).
    libexec.install Dir["*"]
    bin.install_symlink libexec/"agentry"
    # Short aliases. `agy` is kept for back-compat, but Google's Antigravity CLI also
    # claims that name — `agyx` is the short name that cannot collide. Homebrew prints its
    # own caveat when a linked binary is shadowed on PATH.
    bin.install_symlink libexec/"agentry" => "agy"
    bin.install_symlink libexec/"agentry" => "agyx"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/agentry version")
  end
end
