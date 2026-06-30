# Homebrew formula for agentry (agy).
#
# Intended for a tap (e.g. `OpenTechIL/homebrew-tap`): `brew install OpenTechIL/tap/agy`.
# `version` and the per-asset `sha256` values are release-specific — release automation
# fills them from SHA256SUMS.txt (see packaging/README.md). The 64-zero shas below are
# placeholders so the structure is reviewable before the first tagged release.
class Agy < Formula
  desc "Dependency manager for AI coding agents"
  homepage "https://github.com/OpenTechIL/agentry"
  version "0.1.0"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/OpenTechIL/agentry/releases/download/v#{version}/agy-#{version}-macos-arm64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
    on_intel do
      url "https://github.com/OpenTechIL/agentry/releases/download/v#{version}/agy-#{version}-macos-x86_64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/OpenTechIL/agentry/releases/download/v#{version}/agy-#{version}-linux-x86_64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
    # linux-arm64 has no prebuilt binary yet (see install.sh); use `uv tool install`.
  end

  def install
    # The release asset is the bare binary, named agy-<version>-<target>.
    bin.install Dir["agy-*"].first => "agy"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/agy version")
  end
end
