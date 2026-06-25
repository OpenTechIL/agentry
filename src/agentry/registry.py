"""Resolve repos from external catalogs — the "artifactory" front end.

A :class:`~agentry.models.Registry` points at a JSON catalog (a local file or an http(s)
URL) mapping a bare repo name to its source + curated components. ``agy add <name>`` consults
the configured catalogs in order and turns the match into the same Sources + Components a
user would otherwise hand-write — so this adds no new install mechanics, only resolution.

The JSON contract is deliberately the shape a hosted catalog server would serve, so the
local-file form and a future server are interchangeable.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .config import STORE_DIR
from .models import (
    Config,
    Registry,
    RepositoryEntry,
    RepositoryIndex,
)


class RegistryError(RuntimeError):
    pass


def _is_url(location: str) -> bool:
    return location.startswith("http://") or location.startswith("https://")


def _normalize_url(location: str) -> str:
    """Rewrite a GitHub *web* URL to one that serves raw bytes.

    A ``github.com/<owner>/<repo>/blob/<ref>/<path>`` (or ``/raw/``) URL renders an HTML
    page, not the JSON itself — fetching it yields markup or a 404. The raw content lives
    on ``raw.githubusercontent.com``. This lets a user paste the URL straight from their
    browser. Non-GitHub and already-raw URLs pass through unchanged.
    """
    prefix = "https://github.com/"
    if not location.startswith(prefix):
        return location
    rest = location[len(prefix) :]
    parts = rest.split("/")
    # owner / repo / (blob|raw) / ref / path...
    if len(parts) >= 5 and parts[2] in ("blob", "raw"):
        owner, repo, _, ref, *path = parts
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{'/'.join(path)}"
    return location


def _cache_path(root: Path, registry: Registry) -> Path:
    return root / STORE_DIR / "repositories" / f"{registry.name}.json"


def _load_raw(root: Path, registry: Registry) -> str:
    """Fetch a catalog's raw JSON (caching URL fetches under ``.agentry/repositories/``)."""
    if _is_url(registry.location):
        url = _normalize_url(registry.location)
        req = urllib.request.Request(url, headers={"User-Agent": "agentry", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:  # noqa: S310 (http(s) only, gated above)
                raw = resp.read().decode("utf-8")
        except OSError as exc:
            raise RegistryError(f"catalog '{registry.name}': fetch failed: {exc}") from exc
        cache = _cache_path(root, registry)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(raw, encoding="utf-8")
        return raw
    path = Path(registry.location)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        raise RegistryError(f"catalog '{registry.name}': index not found at {path}")
    return path.read_text(encoding="utf-8")


def load_catalog(root: Path, registry: Registry) -> RepositoryIndex:
    """Load (and for URLs, cache under ``.agentry/``) a repository catalog."""
    raw = _load_raw(root, registry)
    try:
        return RepositoryIndex.model_validate(json.loads(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise RegistryError(f"catalog '{registry.name}': invalid index: {exc}") from exc


def find_repo(root: Path, config: Config, name: str) -> tuple[Registry, str, RepositoryEntry] | None:
    """First catalog (in config order) that lists ``name``, with its entry."""
    for registry in config.repositories:
        catalog = load_catalog(root, registry)
        entry = catalog.repositories.get(name)
        if entry is not None:
            return registry, name, entry
    return None


def list_repos(root: Path, config: Config) -> list[tuple[str, str, RepositoryEntry]]:
    """All listed repos across catalogs as ``(catalog_name, repo_name, entry)``."""
    out: list[tuple[str, str, RepositoryEntry]] = []
    seen: set[str] = set()
    for registry in config.repositories:
        catalog = load_catalog(root, registry)
        for repo_name, entry in sorted(catalog.repositories.items()):
            if repo_name in seen:
                continue  # earlier catalog wins
            seen.add(repo_name)
            out.append((registry.name, repo_name, entry))
    return out
