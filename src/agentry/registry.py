"""Resolve repos from external catalogs — the "artifactory" front end.

A :class:`~agentry.models.Registry` points at a JSON catalog (a local file or an http(s)
URL) mapping a bare repo name to its source + curated components. ``agentry add <name>`` consults
the configured catalogs in order and turns the match into the same Sources + Components a
user would otherwise hand-write — so this adds no new install mechanics, only resolution.

The JSON contract is deliberately the shape a hosted catalog server would serve, so the
local-file form and a future server are interchangeable.
"""

from __future__ import annotations

import contextlib
import json
import urllib.request
from collections.abc import Iterable
from pathlib import Path

from . import marketplace
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
from .naming import repo_basename

#: Seconds to wait for a catalog fetch. urllib's default is the *global socket* timeout,
#: i.e. none — without this a hung catalog host blocks ``add``/``list``/``sync`` forever,
#: and ``init`` now registers a remote catalog by default.
FETCH_TIMEOUT = 15.0

#: Hard cap on a catalog response. A catalog is a small JSON index; anything larger is a
#: misconfigured URL or a hostile host, and the body gets written to the store cache.
MAX_CATALOG_BYTES = 8 * 1024 * 1024


class RegistryError(RuntimeError):
    pass


def _is_url(location: str) -> bool:
    return location.startswith(("http://", "https://"))


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


MARKETPLACE_JSON = ".claude-plugin/marketplace.json"
_REPO_HOSTS = ("https://github.com/", "https://gitlab.com/", "https://bitbucket.org/")


def _repo_location(location: str) -> tuple[str, str | None, str] | None:
    """``(repo url, ref, subdir)`` when ``location`` is a browser repo or tree URL on a known
    host — a marketplace *root*. ``None`` for anything else (a file URL included), which is
    fetched as-is, exactly as before."""
    if not location.startswith(_REPO_HOSTS) or location.rstrip("/").endswith(".json"):
        return None
    if any(seg in location for seg in ("/blob/", "/raw/")):
        return None
    clean, ref, subdir, _ = parse_repo_url(location)
    host_path = clean.split("://", 1)[1].split("/", 1)
    if len(host_path) < 2 or "/" not in host_path[1].strip("/"):
        return None  # needs at least <owner>/<repo>
    return clean, ref, subdir or ""


def _raw_marketplace_url(repo: str, ref: str | None, subdir: str) -> str:
    """The raw-bytes URL of a repo's marketplace.json (``HEAD`` = its default branch)."""
    path = "/".join(p for p in (subdir.strip("/"), MARKETPLACE_JSON) if p)
    at = ref or "HEAD"
    if repo.startswith("https://github.com/"):
        owner_repo = repo[len("https://github.com/") :]
        return f"https://raw.githubusercontent.com/{owner_repo}/{at}/{path}"
    if repo.startswith("https://gitlab.com/"):
        return f"{repo}/-/raw/{at}/{path}"
    return f"{repo}/raw/{at}/{path}"


def _marketplace_base(root: Path, registry: Registry) -> marketplace.MarketBase:
    """Where a marketplace catalog came from, so relative plugin paths can be resolved."""
    location = registry.location
    repo = _repo_location(location)
    if repo is not None:
        url, ref, subdir = repo
        return marketplace.MarketBase("git", root=subdir, url=url, ref=ref)
    if _is_url(location):
        raw = _normalize_url(location)
        gh = "https://raw.githubusercontent.com/"
        if raw.startswith(gh) and raw.endswith(MARKETPLACE_JSON):
            parts = raw[len(gh) : -len(MARKETPLACE_JSON)].strip("/").split("/")
            if len(parts) >= 3:  # owner / repo / ref / subdir...
                owner, name, ref, *sub = parts
                return marketplace.MarketBase(
                    "git", root="/".join(sub), url=f"https://github.com/{owner}/{name}", ref=ref
                )
        return marketplace.MarketBase("url")
    path = Path(location)
    if not path.is_absolute():
        path = root / path
    if path.is_dir():
        return marketplace.MarketBase("local", root=str(path))
    mroot = path.parent.parent if path.parent.name == ".claude-plugin" else path.parent
    return marketplace.MarketBase("local", root=str(mroot))


def default_branch(url: str) -> str | None:
    """The default branch of the git repo at ``url`` (``git ls-remote --symref``), or
    ``None`` when it can't be read (offline, no access, not a git URL)."""
    from .resolver import ResolveError, _git

    try:
        out = _git(["ls-remote", "--symref", url, "HEAD"])
    except ResolveError:
        return None
    for line in out.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line[len("ref: refs/heads/") : -len("\tHEAD")]
    return None


def _cache_path(root: Path, registry: Registry) -> Path:
    return root / STORE_DIR / "repositories" / f"{registry.name}.json"


def _load_raw(root: Path, registry: Registry) -> str:
    """Fetch a catalog's raw JSON (caching URL fetches under ``.agentry/repositories/``)."""
    repo = _repo_location(registry.location)
    if repo is not None or _is_url(registry.location):
        url = _raw_marketplace_url(*repo) if repo is not None else _normalize_url(registry.location)
        # S310: the scheme is gated by _is_url() above and re-checked after redirects below.
        req = urllib.request.Request(  # noqa: S310
            url, headers={"User-Agent": "agentry", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310
                # Re-check after redirects: the pre-flight check only covered the URL we
                # asked for, and a 3xx can hand us back any scheme urllib is willing to open.
                final = getattr(resp, "url", url) or url
                if not _is_url(final):
                    raise RegistryError(
                        f"catalog '{registry.name}': refused redirect to non-http(s) {final!r}"
                    )
                # read one byte past the cap so an over-size body is detected, not truncated.
                body = resp.read(MAX_CATALOG_BYTES + 1)
                if len(body) > MAX_CATALOG_BYTES:
                    raise RegistryError(
                        f"catalog '{registry.name}': response exceeds "
                        f"{MAX_CATALOG_BYTES // (1024 * 1024)} MiB — refusing to cache it"
                    )
                raw = body.decode("utf-8")
        except OSError as exc:
            raise RegistryError(f"catalog '{registry.name}': fetch failed: {exc}") from exc
        cache = _cache_path(root, registry)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(raw, encoding="utf-8")
        return raw
    path = Path(registry.location)
    if not path.is_absolute():
        path = root / path
    if path.is_dir():  # a marketplace root
        path = path / MARKETPLACE_JSON
    if not path.is_file():
        raise RegistryError(f"catalog '{registry.name}': index not found at {path}")
    return path.read_text(encoding="utf-8")


#: Per-process memo for :func:`load_catalog`, keyed by ``(root, name, location)``.
#: ``find_repo``/``list_repos``/``find_target``/``list_targets`` each loop over every
#: configured catalog, and a single CLI command calls several of them — without this, one
#: `agentry add` issued the same HTTP fetch three or four times. It also makes failure
#: deterministic within a command: a catalog that resolved once won't intermittently fail
#: later in the same run.
_CATALOG_CACHE: dict[tuple[str, str, str], RepositoryIndex] = {}
#: Translator warnings for marketplace catalogs, same key, by plugin name (``""`` = catalog).
_CATALOG_WARNINGS: dict[tuple[str, str, str], dict[str, list[str]]] = {}


def _memo_key(root: Path, registry: Registry) -> tuple[str, str, str]:
    """Memo key; a local catalog file's mtime is part of it, so an edited file reloads."""
    location = registry.location
    if not _is_url(location) and _repo_location(location) is None:
        path = Path(location) if Path(location).is_absolute() else root / location
        if path.is_dir():
            path = path / MARKETPLACE_JSON
        with contextlib.suppress(OSError):
            location = f"{location}@{path.stat().st_mtime_ns}"
    return (str(root), registry.name, location)


def clear_catalog_cache() -> None:
    """Drop the in-process catalog memo. For tests, and after mutating ``repositories``."""
    _CATALOG_CACHE.clear()
    _CATALOG_WARNINGS.clear()


def catalog_warnings(root: Path, registry: Registry, plugin: str | None = None) -> list[str]:
    """What translating a marketplace catalog skipped or ignored (empty for a plain index).

    Loads the catalog if needed. ``plugin`` narrows to one plugin's warnings.
    """
    load_catalog(root, registry)
    by_plugin = _CATALOG_WARNINGS.get(_memo_key(root, registry), {})
    if plugin is not None:
        return list(by_plugin.get(plugin, []))
    return [w for ws in by_plugin.values() for w in ws]


def load_catalog(root: Path, registry: Registry) -> RepositoryIndex:
    """Load (and for URLs, cache under ``.agentry/``) a repository catalog.

    Memoized for the lifetime of the process — see :data:`_CATALOG_CACHE`. Errors are not
    cached, so a transient failure can be retried within the same run.
    """
    key = _memo_key(root, registry)
    cached = _CATALOG_CACHE.get(key)
    if cached is not None:
        return cached
    raw = _load_raw(root, registry)
    try:
        doc = json.loads(raw)
        if marketplace.is_marketplace(doc):  # a Claude plugin marketplace (ADR-002)
            index, warnings = marketplace.to_index(
                doc, _marketplace_base(root, registry), registry.name
            )
            _CATALOG_WARNINGS[key] = warnings
        else:
            index = RepositoryIndex.model_validate(doc)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RegistryError(f"catalog '{registry.name}': invalid index: {exc}") from exc
    _CATALOG_CACHE[key] = index
    return index


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
        gl_ref, _, gl_rest = tail.partition("/")
        clean = ns
        ref = gl_ref
        subdir = gl_rest or None
    elif clean.startswith(bb):
        parts = clean[len(bb) :].split("/")
        # owner / repo / src / ref / subdir...
        if len(parts) >= 4 and parts[2] == "src":
            owner, repo, _, ref, *rest = parts
            clean = f"{bb}{owner}/{repo}"
            subdir = "/".join(rest) or None
    name = repo_basename(clean)
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
    # Drop noise the curated file never carries: empty target_profiles, absent expose, and
    # flags left at their default (an explicit non-default value is kept).
    body = repos[name]
    if not body.get("target_profiles"):
        body.pop("target_profiles", None)
    if body.get("expose") is None:
        body.pop("expose", None)
    if body.get("copy") is False:
        body.pop("copy", None)
    if body.get("namespaced") is True:
        body.pop("namespaced", None)
    src = body.get("source", {})
    for key, default in (("pinned", False), ("default_branch", False), ("profiles", {})):
        if src.get(key) == default:
            src.pop(key, None)
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
    active_targets: Iterable[str],
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

    Order is deterministic, since it becomes the key order in ``.agentry.yml``: targets in
    the order given (pass ``config.targets``; an unordered set is sorted), types in
    :class:`ComponentType` order.
    """
    drivers = BUILTIN_DRIVERS if drivers is None else drivers
    profiles: dict[str, dict[ComponentType, ProfileRule]] = {
        t: dict(rules) for t, rules in entry.target_profiles.items()
    }
    if not (entry.copy_install or entry.namespaced):
        return profiles

    shipped = {c.type for c in comps}
    present = [t for t in ComponentType if t in shipped and t in LINK_TYPES]
    targets = (
        sorted(active_targets)
        if isinstance(active_targets, (set, frozenset))
        else list(dict.fromkeys(active_targets))
    )
    for target in targets:
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
    root: Path, config: Config, name: str, *, catalog_name: str | None = None
) -> tuple[Registry, str, RepositoryEntry] | None:
    """First catalog (in config order) that lists ``name``, with its entry.

    ``catalog_name`` restricts the search to that catalog (``add <catalog>/<plugin>``).
    """
    for registry in config.repositories:
        if catalog_name is not None and registry.name != catalog_name:
            continue
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


def find_target(
    root: Path, config: Config, name: str, *, catalog: str | None = None
) -> tuple[Registry, dict[ComponentType, ProfileRule]] | None:
    """First catalog (in config order) that publishes a driver overlay named ``name``.

    Restrict to one catalog with ``catalog``. Returns the owning registry and the overlay's
    ``ComponentType -> ProfileRule`` map, ready for ``ConfigStore.merge_target_profiles``.
    """
    for registry in config.repositories:
        if catalog is not None and registry.name != catalog:
            continue
        profile = load_catalog(root, registry).targets.get(name)
        if profile is not None:
            return registry, profile
    return None


def list_targets(
    root: Path, config: Config
) -> list[tuple[str, str, dict[ComponentType, ProfileRule]]]:
    """All driver overlays across catalogs as ``(catalog_name, target_name, profile)``."""
    out: list[tuple[str, str, dict[ComponentType, ProfileRule]]] = []
    seen: set[str] = set()
    for registry in config.repositories:
        catalog = load_catalog(root, registry)
        for tname, profile in sorted(catalog.targets.items()):
            if tname in seen:
                continue  # earlier catalog wins
            seen.add(tname)
            out.append((registry.name, tname, profile))
    return out
