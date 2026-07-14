# tests/test_render_homebrew.py
import pytest
from scripts.render_homebrew import parse_sums, render

TEMPLATE = """class Agy < Formula
  version "0.0.0"
  on_macos do
    on_arm do
      url "https://example/agy-#{version}-macos-arm64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
    on_intel do
      url "https://example/agy-#{version}-macos-x86_64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
  end
  on_linux do
    url "https://example/agy-#{version}-linux-x86_64"
    sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  end
end
"""

SUMS = """\
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  agy-1.2.3-macos-arm64
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  agy-1.2.3-macos-x86_64
cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc  agy-1.2.3-linux-x86_64
dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd  agy-1.2.3-windows-x86_64.exe
"""


def test_parse_sums_maps_asset_to_hash():
    sums = parse_sums(SUMS)
    assert sums["agy-1.2.3-macos-arm64"].startswith("aaaa")
    assert sums["agy-1.2.3-linux-x86_64"].startswith("cccc")
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
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  agy-1.2.3-macos-arm64\n"
    )
    with pytest.raises(SystemExit):
        render(TEMPLATE, "1.2.3", partial)
