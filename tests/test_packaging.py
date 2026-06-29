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
