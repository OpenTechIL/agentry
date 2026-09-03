"""PyInstaller entrypoint for the `agentry` CLI.

Kept separate from the console scripts in ``pyproject.toml`` so the frozen binary has a
stable, import-clean entry module.
"""

from agentry.cli import app

if __name__ == "__main__":
    app()
