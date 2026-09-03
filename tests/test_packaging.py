"""Packaging manifests must agree on the shipped command names.

agentry installs under three names: ``agentry`` (canonical, the only real executable in a
frozen build) plus the short aliases ``agy`` and ``agyx``. Each distribution channel ships
one binary and creates the aliases its own way (symlinks on Linux/macOS, ``.cmd`` shims on
Windows, ``install_symlink`` for Homebrew, shim entries for Scoop). These tests are the
guardrail that a rename touched every channel — they assert on names deliberately, because
a missed one only shows up as a broken release asset.
"""

import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

#: The canonical executable name. Every channel ships exactly this binary.
BIN = "agentry"

#: Short aliases every channel must also provide.
ALIASES = ("agy", "agyx")


def test_entry_file_exists():
    assert Path("packaging/entry.py").is_file()


def test_entry_imports_cli_app():
    import importlib.util

    spec = importlib.util.spec_from_file_location("agentry_entry", "packaging/entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # __name__ != "__main__", so app() is not invoked
    from agentry.cli import app

    assert module.app is app


def test_pyinstaller_spec_builds_the_canonical_binary():
    text = Path("packaging/agentry.spec").read_text(encoding="utf-8")
    assert f"name='{BIN}'" in text or f'name="{BIN}"' in text


def test_devcontainer_feature_is_valid_json():
    """The devcontainer Feature manifest must be valid JSON with the spec's required keys."""
    path = Path("packaging/devcontainer/src/agentry/devcontainer-feature.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    # devcontainers.io requires at least an id and version.
    assert data["id"] == "agentry"
    assert data["version"]
    # It must install agentry and reconcile on create.
    assert f"{BIN} sync --frozen" in data["postCreateCommand"]


def test_scoop_manifest_ships_the_binary_and_both_aliases():
    data = json.loads(Path("packaging/scoop/agentry.json").read_text(encoding="utf-8"))
    assert data["version"]
    # Scoop's shim-alias form: a bare exe, then [exe, alias] pairs.
    assert data["bin"][0] == f"{BIN}.exe"
    shims = {entry[1] for entry in data["bin"][1:]}
    assert shims == set(ALIASES)
    assert all(entry[0] == f"{BIN}.exe" for entry in data["bin"][1:])


def test_nfpm_installs_the_binary_and_symlinks_both_aliases():
    data = YAML(typ="safe").load(Path("packaging/nfpm.yaml").read_text(encoding="utf-8"))
    assert data["name"] == BIN
    assert data["arch"] == "amd64"
    # Version is injected from the environment at build time, not hard-coded.
    assert data["version"] == "${VERSION}"

    by_dst = {c["dst"]: c for c in data["contents"]}
    assert by_dst[f"/usr/bin/{BIN}"]["src"] == f"./dist/{BIN}"
    for alias in ALIASES:
        entry = by_dst[f"/usr/bin/{alias}"]
        assert entry["type"] == "symlink"
        assert entry["src"] == f"/usr/bin/{BIN}"


def test_homebrew_formula_installs_the_binary_and_both_aliases():
    text = Path("packaging/homebrew/agentry.rb").read_text(encoding="utf-8")
    assert "class Agentry < Formula" in text
    assert f'bin.install Dir["{BIN}-*"].first => "{BIN}"' in text
    for alias in ALIASES:
        assert f'bin.install_symlink bin/"{BIN}" => "{alias}"' in text
    # The formula's own test must exercise the canonical name.
    assert f'shell_output("#{{bin}}/{BIN} version")' in text


def test_macos_pkg_distribution_is_per_user():
    """The macOS productbuild distribution must build a per-user, versioned installer."""
    text = Path("packaging/macos/distribution.xml").read_text(encoding="utf-8")
    # Per-user install (no admin): payload goes into the user's home.
    assert 'enable_currentUserHome="true"' in text
    # References the component pkg productbuild wraps.
    assert f"{BIN}-component.pkg" in text
    # Version is injected at build time, not hard-coded.
    assert "${VERSION}" in text
    # The PATH-fixup script the pkg runs must exist.
    assert Path("packaging/macos/scripts/postinstall").is_file()


def test_macos_postinstall_creates_the_aliases():
    text = Path("packaging/macos/scripts/postinstall").read_text(encoding="utf-8")
    assert f'ln -sf "$BIN_DIR/{BIN}"' in text
    assert f"for alias_name in {' '.join(ALIASES)}" in text


def test_inno_installer_script_ships_the_binary_and_alias_shims():
    """The Inno Setup script must build a versioned, per-user agentry installer."""
    text = Path("packaging/windows/agentry.iss").read_text(encoding="utf-8")
    # Version is passed on the ISCC command line (/DMyAppVersion=...).
    assert "MyAppVersion" in text
    # Per-user install (no admin) matching install.ps1.
    assert "PrivilegesRequired=lowest" in text
    assert f"OutputBaseFilename={BIN}-{{#MyAppVersion}}-windows-x86_64-setup" in text
    assert f'#define MyAppExe "{BIN}.exe"' in text
    # Windows has no unprivileged symlink, so the aliases are .cmd shims.
    for alias in ALIASES:
        assert f"WriteAliasShim('{alias}');" in text


@pytest.mark.parametrize(
    ("script", "quote"),
    [
        ("install.sh", 'mv "$tmp/agentry" "$INSTALL_DIR/agentry"'),
        ("install.ps1", 'Move-Item -Force "$tmp\\agentry.exe" "$InstallDir\\agentry.exe"'),
    ],
)
def test_install_scripts_place_the_canonical_binary(script, quote):
    assert quote in Path(script).read_text(encoding="utf-8")


@pytest.mark.parametrize("script", ["install.sh", "install.ps1"])
def test_install_scripts_fall_back_to_the_legacy_asset_name(script):
    """Assets were named agy-<version>-<target> before 0.1.4.

    An older copy of the installer must still resolve a new release, and a new copy must
    still resolve an old one — so both names are tried, current first.
    """
    text = Path(script).read_text(encoding="utf-8")
    agentry_at = text.index(f"{BIN}-$")  # "agentry-${version_no_v}" / "agentry-$version_no_v"
    legacy_at = text.index("agy-$")
    assert agentry_at < legacy_at, "the current asset name must be preferred"


@pytest.mark.parametrize("script", ["install.sh", "install.ps1"])
def test_install_scripts_create_both_aliases(script):
    text = Path(script).read_text(encoding="utf-8")
    for alias in ALIASES:
        assert alias in text
