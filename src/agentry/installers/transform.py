"""Transform strategy — copy-with-rewrite.

Materialize a component as a **committed real file** whose content is rewritten by a provider,
instead of a live symlink. The opt-in path for the cases that need content translation (Phase 3
of the transform-seam design); untransformed components keep their live symlink. Like the copy
strategy it refuses to clobber a file it doesn't own, and removal goes through ``copy.remove_copy``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..emit import _strip_frontmatter, run_agent

STRIP_FRONTMATTER = "strip-frontmatter"
AGENT = "agent"

_DEFAULT_AGENT_PROMPT = (
    "Rewrite the following component for portability across AI coding agents, preserving every"
    " concrete instruction. Output ONLY the rewritten content — no preamble, no code fences."
)


def render(artifact: Path, provider: str, prompt: str | None, *, command: list[str]) -> str:
    """Produce the transformed content for ``artifact`` under ``provider``."""
    text = artifact.read_text(encoding="utf-8")
    if provider == STRIP_FRONTMATTER:
        return _strip_frontmatter(text).lstrip("\n")
    if provider == AGENT:
        instruction = prompt or _DEFAULT_AGENT_PROMPT
        return run_agent(command, f"{instruction}\n\n--- CONTENT ---\n{text}")
    raise ValueError(f"unknown transform provider: {provider}")


def install_transform(root: Path, content: str, dest_rel: str, *, managed: bool) -> str:
    """Write ``content`` to ``dest_rel`` as a real file. Returns created/updated/exists.

    Refuses to overwrite a path agentry doesn't already manage (the never-clobber invariant).
    """
    dest = root / dest_rel
    if dest.is_symlink() or dest.exists():
        if not managed:
            raise FileExistsError(
                f"{dest_rel} exists and isn't managed by agentry — refusing to overwrite"
            )
        if dest.is_file() and not dest.is_symlink() and dest.read_text(encoding="utf-8") == content:
            return "exists"
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.is_dir():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return "updated"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return "created"


def transform_state(root: Path, dest_rel: str) -> str:
    """Drift check: ``"ok"`` if the transformed file is present, else ``"missing"``."""
    dest = root / dest_rel
    return "ok" if dest.is_file() and not dest.is_symlink() else "missing"
