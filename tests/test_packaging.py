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
