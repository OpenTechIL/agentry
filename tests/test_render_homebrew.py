# tests/test_render_homebrew.py
import pytest
from scripts.render_homebrew import parse_sums, render

TEMPLATE = """class Agentry < Formula
  version "0.0.0"
  on_macos do
    on_arm do
      url "https://example/agentry-#{version}-macos-arm64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
    on_intel do
      url "https://example/agentry-#{version}-macos-x86_64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
  end
  on_linux do
    url "https://example/agentry-#{version}-linux-x86_64"
    sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  end
end
"""

SUMS = """\
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  agentry-1.2.3-macos-arm64
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  agentry-1.2.3-macos-x86_64
cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc  agentry-1.2.3-linux-x86_64
dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd  agentry-1.2.3-windows-x86_64.exe
"""


def test_parse_sums_maps_asset_to_hash():
    sums = parse_sums(SUMS)
    assert sums["agentry-1.2.3-macos-arm64"].startswith("aaaa")
    assert sums["agentry-1.2.3-linux-x86_64"].startswith("cccc")
    assert len(sums) == 4


def test_render_fills_version_and_distinct_shas_by_target():
    out = render(TEMPLATE, "1.2.3", parse_sums(SUMS))
    assert 'version "1.2.3"' in out
    assert "0.0.0" not in out
    # every placeholder replaced — no zero shas remain
    assert "0000000000000000" not in out
    # each sha lands under the block whose url names its target
    arm = out.index("macos-arm64")
    intel = out.index("macos-x86_64")
    linux = out.index("linux-x86_64")
    assert out.index('"aaaa', arm) < intel
    assert out.index('"bbbb', intel) < linux
    assert '"cccc' in out[linux:]


def test_render_raises_on_missing_sha():
    partial = parse_sums(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  agentry-1.2.3-macos-arm64\n"
    )
    with pytest.raises(SystemExit):
        render(TEMPLATE, "1.2.3", partial)


TARBALL_TEMPLATE = (
    TEMPLATE.replace('macos-arm64"', 'macos-arm64.tar.gz"')
    .replace('macos-x86_64"', 'macos-x86_64.tar.gz"')
    .replace('linux-x86_64"', 'linux-x86_64.tar.gz"')
)

TARBALL_SUMS = """\
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  agentry-1.2.3-macos-arm64.tar.gz
1111111111111111111111111111111111111111111111111111111111111111  agentry-1.2.3-macos-arm64
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  agentry-1.2.3-macos-x86_64.tar.gz
cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc  agentry-1.2.3-linux-x86_64.tar.gz
"""


def test_render_keys_tarball_urls_off_the_tarball_hash():
    """The formula ships the one-folder tarball; its sha is the tarball's, not the bare
    binary's that sits beside it in SHA256SUMS.txt."""
    out = render(TARBALL_TEMPLATE, "1.2.3", parse_sums(TARBALL_SUMS))
    assert "0000000000000000" not in out
    assert "1111111111111111" not in out
    arm = out.index("macos-arm64.tar.gz")
    assert out.index('"aaaa', arm) < out.index("macos-x86_64.tar.gz")


def test_shipped_formula_renders_against_tarball_sums():
    from pathlib import Path

    template = Path("packaging/homebrew/agentry.rb").read_text(encoding="utf-8")
    out = render(template, "1.2.3", parse_sums(TARBALL_SUMS))
    assert 'version "1.2.3"' in out
    assert "0000000000000000" not in out
