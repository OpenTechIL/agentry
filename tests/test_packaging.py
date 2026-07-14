"""The PyInstaller entrypoint must import the real CLI app, not redefine it."""

from pathlib import Path


def test_entry_file_exists():
    assert Path("packaging/entry.py").is_file()


def test_entry_imports_cli_app():
    import importlib.util

    spec = importlib.util.spec_from_file_location("agy_entry", "packaging/entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # __name__ != "__main__", so app() is not invoked
    from agentry.cli import app

    assert module.app is app


def test_spec_file_exists():
    text = Path("packaging/agy.spec").read_text()
    assert "name='agy'" in text or 'name="agy"' in text


def test_devcontainer_feature_is_valid_json():
    """The devcontainer Feature manifest must be valid JSON with the spec's required keys."""
    import json

    path = Path("packaging/devcontainer/src/agentry/devcontainer-feature.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    # devcontainers.io requires at least an id and version.
    assert data["id"] == "agentry"
    assert data["version"]
    # It must install agy and reconcile on create.
    assert "agy sync --frozen" in data["postCreateCommand"]


def test_scoop_manifest_is_valid_json():
    import json

    data = json.loads(Path("packaging/scoop/agy.json").read_text(encoding="utf-8"))
    assert data["version"]
    assert data["bin"]  # the installed executable


def test_nfpm_config_builds_agy_packages():
    """The nfpm config must install the frozen binary to /usr/bin/agy."""
    from ruamel.yaml import YAML

    data = YAML(typ="safe").load(Path("packaging/nfpm.yaml").read_text(encoding="utf-8"))
    assert data["name"] == "agy"
    assert data["arch"] == "amd64"
    # Version is injected from the environment at build time, not hard-coded.
    assert data["version"] == "${VERSION}"
    dsts = {c["dst"] for c in data["contents"]}
    assert "/usr/bin/agy" in dsts


def test_macos_pkg_distribution_is_per_user():
    """The macOS productbuild distribution must build a per-user, versioned installer."""
    text = Path("packaging/macos/distribution.xml").read_text(encoding="utf-8")
    # Per-user install (no admin): payload goes into the user's home.
    assert 'enable_currentUserHome="true"' in text
    # References the component pkg productbuild wraps.
    assert "agy-component.pkg" in text
    # Version is injected at build time, not hard-coded.
    assert "${VERSION}" in text
    # The PATH-fixup script the pkg runs must exist.
    assert Path("packaging/macos/scripts/postinstall").is_file()


def test_inno_installer_script_exists():
    """The Inno Setup script must build a versioned, per-user agy installer."""
    text = Path("packaging/windows/agy.iss").read_text(encoding="utf-8")
    # Version is passed on the ISCC command line (/DMyAppVersion=...).
    assert "MyAppVersion" in text
    # Per-user install (no admin) matching install.ps1.
    assert "PrivilegesRequired=lowest" in text
    assert "OutputBaseFilename=agy-{#MyAppVersion}-windows-x86_64-setup" in text
