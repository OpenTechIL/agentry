"""Resolve skills from external indexes — the "artifactory" front end.

A :class:`~agentry.models.Registry` points at a JSON index (a local file or an http(s)
URL) mapping a bare skill name to its source + install method. ``agy add <name>`` consults
the configured registries in order and turns the match into the same Source + Component a
user would otherwise hand-write — so this adds no new install mechanics, only resolution.

The JSON contract is deliberately the shape a hosted registry server would serve, so the
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
    RegistryIndex,
    RegistrySkill,
    RepositoryEntry,
    RepositoryIndex,
)


class RegistryError(RuntimeError):
    pass


def _is_url(location: str) -> bool:
    return location.startswith("http://") or location.startswith("https://")


def _cache_path(root: Path, registry: Registry, kind: str) -> Path:
    return root / STORE_DIR / kind / f"{registry.name}.json"


def _load_raw(root: Path, registry: Registry, kind: str) -> str:
    """Fetch a catalog/index's raw JSON (caching URL fetches under ``.agentry/<kind>/``)."""
    if _is_url(registry.location):
        try:
            with urllib.request.urlopen(registry.location) as resp:  # noqa: S310 (http(s) only, gated above)
                raw = resp.read().decode("utf-8")
        except OSError as exc:
            raise RegistryError(f"registry '{registry.name}': fetch failed: {exc}") from exc
        cache = _cache_path(root, registry, kind)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(raw, encoding="utf-8")
        return raw
    path = Path(registry.location)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        raise RegistryError(f"registry '{registry.name}': index not found at {path}")
    return path.read_text(encoding="utf-8")


def load_index(root: Path, registry: Registry) -> RegistryIndex:
    """Load (and for URLs, cache under ``.agentry/``) a skill registry's index."""
    raw = _load_raw(root, registry, "registries")
    try:
        return RegistryIndex.model_validate(json.loads(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise RegistryError(f"registry '{registry.name}': invalid index: {exc}") from exc


def find(root: Path, config: Config, name: str) -> tuple[Registry, RegistrySkill] | None:
    """First registry (in config order) that lists ``name``, with its entry."""
    for registry in config.registries:
        index = load_index(root, registry)
        skill = index.skills.get(name)
        if skill is not None:
            return registry, skill
    return None


def list_skills(root: Path, config: Config) -> list[tuple[str, str, RegistrySkill]]:
    """All listed skills across registries as ``(registry_name, skill_name, entry)``."""
    out: list[tuple[str, str, RegistrySkill]] = []
    seen: set[str] = set()
    for registry in config.registries:
        index = load_index(root, registry)
        for skill_name, entry in sorted(index.skills.items()):
            if skill_name in seen:
                continue  # earlier registry wins
            seen.add(skill_name)
            out.append((registry.name, skill_name, entry))
    return out


# -- repository catalogs --------------------------------------------------


def load_catalog(root: Path, registry: Registry) -> RepositoryIndex:
    """Load (and for URLs, cache under ``.agentry/``) a repository catalog."""
    raw = _load_raw(root, registry, "repositories")
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
