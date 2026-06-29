"""PyInstaller entrypoint for the `agy` CLI.

Kept separate from the ``agy`` console-script in ``pyproject.toml`` so the
frozen binary has a stable, import-clean entry module.
"""

from agentry.cli import app

if __name__ == "__main__":
    app()
