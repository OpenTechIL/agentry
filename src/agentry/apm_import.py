"""Import a Microsoft **apm** project (`apm.yml`) into agentry's model.

apm and agentry are the same category — a dependency manager for AI-agent context — built on
the same open standards, so an apm manifest is largely *consumable* by agentry. This module is
a one-shot, offline translator: it turns an ``apm.yml`` into agentry ``sources`` + ``components``
+ ``targets`` (and MCP fragments), so a team can evaluate agentry on a real apm repo in seconds.

What maps cleanly (deterministic, no network):

* ``dependencies.apm`` git shorthand ``[host/]owner/repo/<typedir>/<name>[@|#ref]`` → an agentry
  **git source** (the repo root) plus a **component** ``(<type>, <name>)``. agentry resolves a
  ``(type, name)`` to ``<root>/<typedir>/<name>`` by convention, so no subdir/path is needed.
* ``dependencies.apm`` local paths (``./pkg``) → a **local source**.
* ``dependencies.mcp`` inline servers → agentry **MCP fragments** (``{name: {command,args,env}}``
  for stdio, ``{name: {url}}`` for http) — the CLI writes these to a local source and enables
  one ``mcp`` component each.
* ``targets`` → agentry ``targets`` (open strings; passed through).

What can't be inferred offline (whole-repo deps, ``plugins/*`` bundles, full git URLs without a
component subpath, marketplace/bundle specs) becomes a **source + a warning** telling the user to
run ``agy add`` / ``agy list`` — honest over silently guessing. ``includes: auto`` (apm's local
``.apm/`` shipping) is reported, not translated; consuming an on-disk ``.apm/`` tree is separate.

Pure: :func:`translate_apm` performs no I/O. The CLI layer writes files and the config.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Component, ComponentType, Source, SourceType

#: apm primitive directory → agentry component type. apm "prompts" are agentry "commands";
#: apm "instructions" have no agentry component type (a repo-wide doc, not a placed artifact).
APM_TYPEDIR: dict[str, ComponentType] = {
    "skills": ComponentType.SKILL,
    "agents": ComponentType.AGENT,
    "commands": ComponentType.COMMAND,
    "prompts": ComponentType.COMMAND,
    "tools": ComponentType.TOOL,
    "hooks": ComponentType.HOOK,
    "mcp": ComponentType.MCP,
}

#: Default git host for an apm shorthand that omits one (``owner/repo`` → github.com).
_DEFAULT_HOST = "github.com"


@dataclass
class ApmImport:
    """Result of translating an ``apm.yml`` — ready for the CLI to apply."""

    targets: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    #: server name → MCP fragment (``{name: {...}}``), to be written into a local source.
    mcp_fragments: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ParsedDep:
    kind: str  # "git" | "local" | "marketplace" | "bundle" | "url"
    url: str | None = None  # git clone URL (git/url)
    path: str | None = None  # local path (local)
    ref: str | None = None  # branch/tag/commit/semver
    repo: str | None = None  # derived source name
    typedir: str | None = None  # leading subpath segment (git shorthand only)
    component: str | None = None  # leaf name (git shorthand with a typedir/name subpath)
    raw: str = ""


def _split_ref(body: str) -> tuple[str, str | None]:
    """Peel a trailing ``#ref`` (or ``@ref`` when it isn't a marketplace spec) off a dep body."""
    if "#" in body:
        head, ref = body.rsplit("#", 1)
        return head, ref or None
    return body, None


def parse_apm_dep(spec: str) -> _ParsedDep:
    """Parse one ``dependencies.apm`` entry. Pure — no I/O, no host assumptions beyond the
    documented shorthand. See module docstring for the supported shapes."""
    spec = spec.strip()
    # Bundle archive (checked before local path so ``./bundle.zip`` is a bundle, not a dir).
    if spec.endswith((".zip", ".tar.gz", ".tgz")):
        return _ParsedDep(kind="bundle", raw=spec)

    # Local path: ./x, ../x, /abs, ~/x
    if spec.startswith(("./", "../", "/", "~")):
        name = spec.rstrip("/").rsplit("/", 1)[-1] or spec
        return _ParsedDep(kind="local", path=spec, repo=name, raw=spec)

    # Marketplace: NAME@MARKETPLACE[#ref] — an '@' whose left side has no '/'.
    at = spec.find("@")
    if at != -1 and "/" not in spec[:at]:
        return _ParsedDep(kind="marketplace", raw=spec)

    body, ref = _split_ref(spec)
    # Allow a trailing @ref on a slash-bearing shorthand (owner/repo@v1).
    if ref is None and "@" in body and "/" in body.split("@", 1)[0]:
        body, ref = body.rsplit("@", 1)

    # Full git URL (https / ssh / scp-like).
    if "://" in body or body.startswith("git@"):
        tail = body.rsplit("/", 1)[-1]
        repo = tail[:-4] if tail.endswith(".git") else tail
        return _ParsedDep(kind="url", url=body, ref=ref, repo=repo, raw=spec)

    # Shorthand: [host/]owner/repo[/subpath...]. A first segment containing '.' is the host.
    parts = body.split("/")
    host = _DEFAULT_HOST
    if parts and "." in parts[0]:
        host = parts.pop(0)
    if len(parts) < 2:
        return _ParsedDep(kind="bundle", raw=spec)  # unrecognized — flagged as unmapped below
    owner, repo, *subpath = parts
    url = f"https://{host}/{owner}/{repo}"
    typedir = subpath[0] if subpath else None
    component = subpath[1] if len(subpath) >= 2 else None
    return _ParsedDep(
        kind="git",
        url=url,
        ref=ref,
        repo=repo,
        typedir=typedir,
        component=component,
        raw=spec,
    )


def _mcp_fragment(entry: dict) -> tuple[str, dict] | None:
    """Translate one ``dependencies.mcp`` object into ``(name, fragment)``; None if unusable."""
    name = entry.get("name")
    if not name:
        return None
    body: dict = {}
    if entry.get("url"):  # http/sse transport
        body["url"] = entry["url"]
        if entry.get("headers"):
            body["headers"] = entry["headers"]
    else:  # stdio
        if entry.get("command"):
            body["command"] = entry["command"]
        if entry.get("args"):
            body["args"] = entry["args"]
    if entry.get("env"):
        body["env"] = entry["env"]
    return name, {name: body}


def translate_apm(doc: dict) -> ApmImport:
    """Translate a parsed ``apm.yml`` document into an :class:`ApmImport`. Pure (no I/O)."""
    out = ApmImport()
    if not isinstance(doc, dict):
        out.warnings.append("apm.yml is not a mapping; nothing imported")
        return out

    targets = doc.get("targets")
    if isinstance(targets, list):
        out.targets = [str(t) for t in targets if t]

    deps = doc.get("dependencies") or {}
    seen_sources: set[str] = set()

    def _add_source(src: Source) -> None:
        if src.name not in seen_sources:
            out.sources.append(src)
            seen_sources.add(src.name)

    for spec in deps.get("apm") or []:
        if not isinstance(spec, str):
            out.warnings.append(f"skipped non-string apm dependency: {spec!r}")
            continue
        dep = parse_apm_dep(spec)
        if dep.kind == "marketplace":
            out.warnings.append(f"'{spec}': marketplace specs aren't supported — skipped")
            continue
        if dep.kind == "bundle":
            out.warnings.append(f"'{spec}': bundle/unrecognized spec — skipped")
            continue
        if dep.kind == "local":
            _add_source(Source(name=dep.repo, type=SourceType.LOCAL, path=dep.path))
            out.warnings.append(
                f"'{spec}': added local source '{dep.repo}' — run `agy add {dep.repo}/<type>/<name>`"
                " (or `agy list`) to enable its components"
            )
            continue
        # git / url
        ref = dep.ref or "main"
        _add_source(Source(name=dep.repo, type=SourceType.GIT, url=dep.url, ref=ref))
        if dep.kind == "git" and dep.typedir in APM_TYPEDIR and dep.component:
            out.components.append(
                Component(source=dep.repo, type=APM_TYPEDIR[dep.typedir], name=dep.component)
            )
        else:
            hint = f" (subpath '{dep.typedir}/{dep.component}')" if dep.typedir else ""
            out.warnings.append(
                f"'{spec}': added source '{dep.repo}'{hint} but couldn't infer a single "
                f"component — run `agy add {dep.repo}/<type>/<name>` or `agy list`"
            )

    for entry in deps.get("mcp") or []:
        if not isinstance(entry, dict):
            out.warnings.append(f"skipped non-object mcp dependency: {entry!r}")
            continue
        frag = _mcp_fragment(entry)
        if frag is None:
            out.warnings.append(f"skipped mcp entry without a name: {entry!r}")
            continue
        name, body = frag
        out.mcp_fragments[name] = body

    if doc.get("includes") == "auto":
        out.warnings.append(
            "'includes: auto' (local .apm/ content) is not translated — author it as an agentry"
            " source, or consume the .apm/ tree directly (separate interop)"
        )
    return out
