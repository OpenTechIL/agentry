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
from .drivers import BUILTIN_DRIVERS, Driver
from .models import (
    LINK_TYPES,
    Component,
    ComponentType,
    Config,
    ProfileRule,
    Registry,
    RepositoryEntry,
    RepositoryIndex,
    Strategy,
)


class RegistryError(RuntimeError):
    pass


def _is_url(location: str) -> bool:
    return location.startswith("http://") or location.startswith("https://")


def _normalize_url(location: str) -> str:
    """Rewrite a browser *web* URL for a catalog JSON to one that serves raw bytes.

    A web URL renders an HTML page, not the JSON — fetching it yields markup or a 404.
    This lets a user paste the URL straight from their browser, across the common hosts:

    * **GitHub** — ``github.com/<o>/<r>/blob/<ref>/<path>`` (or ``/raw/``)
      → ``raw.githubusercontent.com/<o>/<r>/<ref>/<path>``.
    * **GitLab** (incl. nested groups) — ``gitlab.com/<ns…>/-/blob/<ref>/<path>``
      → ``gitlab.com/<ns…>/-/raw/<ref>/<path>`` (raw is served on the same host).
    * **Bitbucket** — ``bitbucket.org/<o>/<r>/src/<ref>/<path>``
      → ``bitbucket.org/<o>/<r>/raw/<ref>/<path>``.

    Already-raw URLs and any other host pass through unchanged — a direct raw URL always
    works (these are only browser-paste niceties). Self-hosted GitLab/Gitea/Gogs are not
    detected by host, so paste their raw URL directly.
    """
    gh = "https://github.com/"
    if location.startswith(gh):
        parts = location[len(gh) :].split("/")
        # owner / repo / (blob|raw) / ref / path...
        if len(parts) >= 5 and parts[2] in ("blob", "raw"):
            owner, repo, _, ref, *path = parts
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{'/'.join(path)}"
        return location
    # GitLab keeps a `/-/` infix between the (possibly nested-group) namespace and the verb.
    if location.startswith("https://gitlab.com/") and "/-/" in location:
        ns, _, tail = location.partition("/-/")
        verb, _, rest = tail.partition("/")  # rest = <ref>/<path...>
        if verb in ("blob", "raw") and rest:
            return f"{ns}/-/raw/{rest}"
        return location
    bb = "https://bitbucket.org/"
    if location.startswith(bb):
        parts = location[len(bb) :].split("/")
        # owner / repo / (src|raw) / ref / path...
        if len(parts) >= 5 and parts[2] in ("src", "raw"):
            owner, repo, _, ref, *path = parts
            return f"{bb}{owner}/{repo}/raw/{ref}/{'/'.join(path)}"
        return location
    return location


def _cache_path(root: Path, registry: Registry) -> Path:
    return root / STORE_DIR / "repositories" / f"{registry.name}.json"


def _load_raw(root: Path, registry: Registry) -> str:
    """Fetch a catalog's raw JSON (caching URL fetches under ``.agentry/repositories/``)."""
    if _is_url(registry.location):
        url = _normalize_url(registry.location)
        req = urllib.request.Request(
            url, headers={"User-Agent": "agentry", "Accept": "application/json"}
        )
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


def parse_repo_url(url: str) -> tuple[str, str | None, str | None, str]:
    """Split a (possibly browser-pasted) repo URL into authoring inputs.

    Returns ``(clean_url, ref, subdir, default_name)``. A browser "tree" URL yields the bare
    repo URL plus the ``ref`` and ``subdir`` it points at, so a user can paste straight from
    their browser:

    * **GitHub** — ``github.com/<o>/<r>/tree/<ref>/<subdir…>``.
    * **GitLab** (incl. nested groups) — ``gitlab.com/<ns…>/-/tree/<ref>/<subdir…>``.
    * **Bitbucket** — ``bitbucket.org/<o>/<r>/src/<ref>/<subdir…>``.

    A plain repo URL passes through with ``ref``/``subdir`` of ``None``. ``default_name`` is
    the repo basename (trailing ``.git`` stripped), used when the CLI name argument is
    omitted. Any git URL clones regardless of host (see :func:`agentry.resolver.resolve`);
    this only adds browser-paste ergonomics for the hosts above.

    This is the repo-URL counterpart to :func:`_normalize_url`, which rewrites *raw-JSON*
    catalog URLs; both let the same browser URL be pasted for different inputs.
    """
    clean = url.rstrip("/")
    ref: str | None = None
    subdir: str | None = None
    gh = "https://github.com/"
    bb = "https://bitbucket.org/"
    if clean.startswith(gh):
        parts = clean[len(gh) :].split("/")
        # owner / repo / tree / ref / subdir...
        if len(parts) >= 4 and parts[2] == "tree":
            owner, repo, _, ref, *rest = parts
            clean = f"{gh}{owner}/{repo}"
            subdir = "/".join(rest) or None
    elif clean.startswith("https://gitlab.com/") and "/-/tree/" in clean:
        # <ns…>/-/tree/<ref>/<subdir…>  (ns may be nested groups)
        ns, _, tail = clean.partition("/-/tree/")
        ref, _, rest = tail.partition("/")
        clean = ns
        subdir = rest or None
    elif clean.startswith(bb):
        parts = clean[len(bb) :].split("/")
        # owner / repo / src / ref / subdir...
        if len(parts) >= 4 and parts[2] == "src":
            owner, repo, _, ref, *rest = parts
            clean = f"{bb}{owner}/{repo}"
            subdir = "/".join(rest) or None
    name = clean.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return clean, ref, subdir, name


def add_entry(
    catalog_path: Path, name: str, entry: RepositoryEntry, *, force: bool = False
) -> None:
    """Insert ``entry`` under ``name`` into the JSON catalog at ``catalog_path``.

    Loads the existing catalog (or starts an empty one), rejects a duplicate ``name`` unless
    ``force``, validates the whole document via :class:`RepositoryIndex`, then writes it back
    as 2-space-indented JSON to match the curated catalog's style.
    """
    if catalog_path.is_file():
        try:
            doc = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise RegistryError(f"catalog {catalog_path}: invalid JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise RegistryError(f"catalog {catalog_path}: expected a JSON object at the top level")
    else:
        doc = {"version": 1, "repositories": {}}
    repos = doc.setdefault("repositories", {})
    if not isinstance(repos, dict):
        raise RegistryError(f"catalog {catalog_path}: 'repositories' must be a JSON object")
    if name in repos and not force:
        raise RegistryError(
            f"repo '{name}' already exists in {catalog_path} (use --force to overwrite)"
        )
    repos[name] = entry.model_dump(
        mode="json", by_alias=True, exclude_none=True, exclude_defaults=False
    )
    # Drop noise the curated file never carries: empty target_profiles, absent expose.
    body = repos[name]
    if not body.get("target_profiles"):
        body.pop("target_profiles", None)
    if body.get("expose") is None:
        body.pop("expose", None)
    try:
        RepositoryIndex.model_validate(doc)
    except ValueError as exc:
        raise RegistryError(f"catalog {catalog_path}: invalid after edit: {exc}") from exc
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=False keeps non-ASCII (e.g. an em-dash in a summary) literal, matching the
    # hand-authored catalog rather than escaping it to \uXXXX.
    catalog_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _namespace_dest(dest: str, repo: str) -> str:
    """Insert a ``<repo>/`` segment before the final path component of a dest template.

    ``.claude/commands/{name}.md`` -> ``.claude/commands/<repo>/{name}.md``.
    """
    parent, sep, last = dest.rpartition("/")
    return f"{parent}/{repo}/{last}" if sep else f"{repo}/{dest}"


def build_install_profiles(
    entry: RepositoryEntry,
    repo: str,
    comps: list[Component],
    active_targets: set[str],
    drivers: dict[str, Driver] | None = None,
) -> dict[str, dict[ComponentType, ProfileRule]]:
    """Resolve a repo entry's ``copy``/``namespaced`` flags into concrete profile rules.

    Starts from the entry's explicit ``target_profiles`` (preserving e.g. a ``hook``
    link+merge rule), then for each file/dir component type the repo actually installs:

    * ``copy`` -> install via the copy strategy instead of the default symlink;
    * ``namespaced`` -> nest dests under ``<repo>/`` for the component types the target's
      driver namespaces (Claude nests command/agent; skills/tools stay flat).

    A rule is synthesized only when the flags change something versus the built-in default,
    so a plain ``copy=false, namespaced=false`` repo adds nothing (the engine's link
    default applies). The result is ready to hand to ``ConfigStore.merge_target_profiles``.
    """
    drivers = BUILTIN_DRIVERS if drivers is None else drivers
    profiles: dict[str, dict[ComponentType, ProfileRule]] = {
        t: dict(rules) for t, rules in entry.target_profiles.items()
    }
    if not (entry.copy_install or entry.namespaced):
        return profiles

    present = {c.type for c in comps if c.type in LINK_TYPES}
    for target in active_targets:
        driver = drivers.get(target)
        for ctype in present:
            existing = profiles.get(target, {}).get(ctype)
            base_dest = driver.spec.link.get(ctype) if driver else None
            dest = existing.dest if existing and existing.dest else base_dest
            if dest is None:
                continue  # target doesn't support this type as link — nothing to synthesize
            strategy = (
                Strategy.COPY
                if entry.copy_install
                else (existing.strategy if existing else Strategy.LINK)
            )
            if entry.namespaced and driver is not None and driver.namespaces(ctype):
                dest = _namespace_dest(dest, repo)
            # Skip a no-op that just restates the built-in link default.
            if existing is None and strategy is Strategy.LINK and dest == base_dest:
                continue
            profiles.setdefault(target, {})[ctype] = ProfileRule(strategy=strategy, dest=dest)
    return profiles


def find_repo(
    root: Path, config: Config, name: str
) -> tuple[Registry, str, RepositoryEntry] | None:
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
