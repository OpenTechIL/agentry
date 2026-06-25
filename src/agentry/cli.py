"""``agy`` — the agentry command-line interface."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.tree import Tree

from . import __version__, deps, discovery
from .config import ConfigStore
from .deps import DependencyError
from .lockfile import load_lock
from .models import Component, ComponentType, GeneratorSpec, Source, SourceType, Target
from .reconcile import SyncResult, status, sync
from .resolver import ResolveError, effective_root, resolve
from .targets import BUILTIN_TARGETS, is_builtin

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="agentry (agy) — a dependency manager for AI coding agents.",
)
source_app = typer.Typer(no_args_is_help=True, help="Manage component sources (git repos / local dirs).")
app.add_typer(source_app, name="source")
repo_app = typer.Typer(no_args_is_help=True, help="Manage repository catalogs (curated source repos).")
app.add_typer(repo_app, name="repo")
registry_app = typer.Typer(no_args_is_help=True, help="Author the curated repository catalog (repositories.json).")
app.add_typer(registry_app, name="registry")

console = Console()
err = Console(stderr=True)


# -- helpers -------------------------------------------------------------


def _root() -> Path:
    return Path.cwd()


def _load() -> ConfigStore:
    try:
        return ConfigStore.load(_root())
    except FileNotFoundError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


def _parse_targets(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        name = v.strip()
        if not name:
            continue
        if not is_builtin(name):
            err.print(
                f"  [yellow]! '{name}' is a custom target — define it under "
                f"target_profiles in .agentry.yml (built-ins: {', '.join(sorted(BUILTIN_TARGETS))}).[/yellow]"
            )
        out.append(name)
    return out


def _parse_ref(ref: str) -> tuple[str, ComponentType, str]:
    parts = ref.split("/")
    if len(parts) != 3:
        err.print(f"[red]Invalid component ref '{ref}'. Expected <source>/<type>/<name>.[/red]")
        raise typer.Exit(1)
    source, ctype_raw, name = parts
    try:
        ctype = ComponentType(ctype_raw)
    except ValueError:
        err.print(f"[red]Unknown type '{ctype_raw}'. Choose from: {', '.join(t.value for t in ComponentType)}[/red]")
        raise typer.Exit(1)
    return source, ctype, name


def _parse_types(values: list[str] | None) -> list[ComponentType]:
    """Validate ``--type`` values against :class:`ComponentType` (skill/agent/command/hook/mcp…)."""
    out: list[ComponentType] = []
    for v in values or []:
        try:
            out.append(ComponentType(v.strip()))
        except ValueError:
            err.print(
                f"[red]Unknown type '{v}'. Choose from: {', '.join(t.value for t in ComponentType)}[/red]"
            )
            raise typer.Exit(1)
    return out


def _print_result(res: SyncResult) -> None:
    for name, sha in res.resolved.items():
        console.print(f"  [dim]resolved[/dim] {name} → [cyan]{sha[:12]}[/cyan]")
    for item in res.created:
        console.print(f"  [green]+ {item}[/green]")
    for item in res.updated:
        console.print(f"  [yellow]~ {item}[/yellow]")
    for item in res.removed:
        console.print(f"  [red]- {item}[/red]")
    for w in res.warnings:
        err.print(f"  [yellow]! {w}[/yellow]")
    if res.gitignore_changed:
        console.print("  [dim]added .agentry/ to .gitignore[/dim]")
    if not (res.created or res.updated or res.removed):
        console.print("  [dim]already up to date[/dim]")


def _add_from_catalog(repo: str, names: list[str], *, types: list[ComponentType], allow_run: bool) -> None:
    """Resolve a repo name via the configured catalogs and install all/selected components.

    ``names`` (from a ``<repo>@a,b`` ref) and ``types`` (from ``--type``) narrow what is
    installed; with neither, a TTY gets an interactive picker and a non-TTY installs everything.
    """
    from . import discovery
    from . import registry as reg
    from .resolver import ResolveError, effective_root, resolve

    store = _load()
    config = store.parsed()
    try:
        match = reg.find_repo(_root(), config, repo) if config.repositories else None
    except reg.RegistryError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if match is None:
        err.print(
            f"[red]No catalog lists '{repo}'.[/red]\n"
            "Add one with `agy repo add <name> <file-or-url>`, or use the full "
            "<source>/<type>/<name> form."
        )
        raise typer.Exit(1)

    _, _, entry = match
    rs = entry.source
    src = (
        Source(name=repo, type=SourceType.GIT, url=rs.url, ref=rs.ref, subdir=rs.subdir)
        if rs.type is SourceType.GIT
        else Source(name=repo, type=SourceType.LOCAL, path=rs.path, subdir=rs.subdir)
    )
    existing = config.source(repo)
    if existing is None:
        store.add_source(src)
    elif (existing.url or existing.path) != (src.url or src.path):
        err.print(f"[red]A different source named '{repo}' already exists; rename or remove it first.[/red]")
        raise typer.Exit(1)

    # Resolve into the store so we can discover what the repo provides.
    try:
        resolve(_root(), src, pinned=None)
    except ResolveError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    # The repo's available components: the curated `expose` set if present (it also carries
    # path/generate for artifacts discovery can't infer), else everything discovery finds.
    if entry.expose:
        available = [
            Component(source=repo, type=e.type, name=e.name, path=e.path, generate=e.generate)
            for e in entry.expose
        ]
    else:
        # Drop per-harness merge variants (e.g. hooks-cursor.json) for harnesses that
        # aren't active targets, so a claude-only install doesn't record foreign-harness
        # fragments it would never use. reconcile skips them regardless; this is hygiene.
        active = set(config.active_targets())
        available = [
            Component(source=repo, type=d.type, name=d.name)
            for d in discovery.discover(effective_root(_root(), src))
            if d.harness is None or d.harness in active
        ]
    if not available:
        err.print(f"[yellow]Repository '{repo}' provided no installable components.[/yellow]")
        raise typer.Exit(1)

    if names or types:
        comps = _select_components(repo, available, names, types)
    elif sys.stdin.isatty():
        comps = _interactive_pick(available)
    else:
        comps = available
    if not comps:
        console.print("[dim]Nothing selected.[/dim]")
        raise typer.Exit(0)

    for comp in comps:
        store.add_component(comp)
    profiles = reg.build_install_profiles(entry, repo, comps, config.active_targets())
    if profiles and store.merge_target_profiles(profiles):
        console.print("  [dim]added target_profiles from catalog (install overrides for this repo)[/dim]")
    store.save()
    console.print(f"[green]Added[/green] {repo} [dim]({len(comps)} component(s) from catalog)[/dim]")
    _do_sync(allow_run=allow_run)


def _select_components(
    repo: str, available: list[Component], names: list[str], types: list[ComponentType]
) -> list[Component]:
    """Narrow ``available`` by ``--type`` then by ``@name``; exit on a name that matches nothing."""
    pool = [c for c in available if c.type in types] if types else available
    if not names:
        return pool
    wanted = set(names)
    selected = [c for c in pool if c.name in wanted]
    missing = wanted - {c.name for c in selected}
    if missing:
        scope = f" of type {', '.join(t.value for t in types)}" if types else ""
        err.print(f"[red]Repository '{repo}' has no component{scope} named: {', '.join(sorted(missing))}.[/red]")
        raise typer.Exit(1)
    return selected


def _interactive_pick(available: list[Component]) -> list[Component]:
    """Prompt the user to pick from ``available``; default installs everything."""
    console.print(f"[bold]{len(available)} component(s) available:[/bold]")
    for i, c in enumerate(available, 1):
        console.print(f"  [cyan]{i:>2}[/cyan]  [magenta]{c.type.value}[/magenta]/{c.name}")
    answer = Prompt.ask(
        "Install which? [dim](numbers comma-separated, 'a' for all, or a type name)[/dim]",
        default="a",
    ).strip().lower()
    if answer in ("a", "all", ""):
        return available
    try:  # a type name installs that whole type
        ctype = ComponentType(answer)
        return [c for c in available if c.type is ctype]
    except ValueError:
        pass
    picks: list[Component] = []
    for tok in answer.split(","):
        tok = tok.strip()
        if tok.isdigit() and 1 <= int(tok) <= len(available):
            picks.append(available[int(tok) - 1])
    return picks


def _do_sync(*, update: bool = False, allow_run: bool = False) -> None:
    try:
        res = sync(_root(), update=update, allow_run=allow_run)
    except (ResolveError, DependencyError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    _print_result(res)


_ALLOW_RUN = typer.Option(
    False,
    "--allow-run",
    help="Permit components with a 'generate' spec to run their own installer commands.",
)


# -- top-level commands --------------------------------------------------


@app.command()
def version() -> None:
    """Print the agentry version."""
    console.print(f"agentry {__version__}")


@app.command()
def init(
    target: list[str] = typer.Option(
        None, "--target", "-t", help="Target AI tool(s): claude, opencode, cursor. Repeatable."
    ),
) -> None:
    """Create .agentry.yml and add .agentry/ to .gitignore."""
    root = _root()
    if ConfigStore.exists(root):
        err.print("[yellow]Already initialized (.agentry.yml exists).[/yellow]")
        raise typer.Exit(1)
    targets = _parse_targets(target) or [Target.CLAUDE]
    store = ConfigStore.create(root, targets)
    store.save()
    from .gitignore import ensure_gitignore

    changed = ensure_gitignore(root)
    console.print(f"[green]Initialized agentry[/green] for targets: {', '.join(targets)}")
    if changed:
        console.print("  [dim]added .agentry/ to .gitignore[/dim]")


@app.command(name="list")
def list_components() -> None:
    """List components discovered across all sources, grouped by source, with their state."""
    store = _load()
    config = store.parsed()
    lock = load_lock(_root())

    declared = {c.ref: c for c in config.components}
    any_rows = False
    for src in config.sources:
        try:
            entry = lock.entry(src.name)
            resolve(_root(), src, pinned=entry.resolved if entry else None)
        except ResolveError as exc:
            err.print(f"  [yellow]! {src.name}: {exc}[/yellow]")
            continue

        # One table per source (plugin), sorted by type then name.
        found = sorted(
            discovery.discover(effective_root(_root(), src)),
            key=lambda d: (d.type.value, d.name),
        )
        if not found:
            continue
        any_rows = True
        table = Table(title=f"{src.name}  ([dim]{len(found)} components[/dim])", show_lines=False)
        table.add_column("type", style="magenta")
        table.add_column("name", style="cyan")
        table.add_column("state")
        for d in found:
            comp = declared.get(f"{src.name}/{d.type.value}/{d.name}")
            if comp is None:
                state = "[dim]available[/dim]"
            elif comp.enabled:
                state = "[green]enabled[/green]"
            else:
                state = "[yellow]disabled[/yellow]"
            table.add_row(d.type.value, d.name, state)
        console.print(table)

    if not any_rows:
        console.print("[dim]No components found. Add a source with `agy source add`.[/dim]")


@app.command(name="search")
def search_components(
    query: str = typer.Argument(None, help="Filter catalog repos by name/summary substring."),
) -> None:
    """Search catalogs for installable repos (and show locally-discovered components)."""
    from . import registry as reg

    store = _load()
    config = store.parsed()
    q = (query or "").lower()
    matched = False
    if config.repositories:
        try:
            repos = reg.list_repos(_root(), config)
        except reg.RegistryError as exc:
            err.print(f"[yellow]! {exc}[/yellow]")
            repos = []
        rows = [
            (rname, cname, entry)
            for cname, rname, entry in repos
            if not q or q in rname.lower() or q in (entry.summary or "").lower()
        ]
        if rows:
            matched = True
            table = Table(title="Catalog repositories")
            table.add_column("repo", style="cyan")
            table.add_column("catalog", style="dim")
            table.add_column("components")
            table.add_column("summary", style="dim")
            for rname, cname, entry in rows:
                scope = f"{len(entry.expose)} curated" if entry.expose else "whole repo"
                table.add_row(rname, cname, scope, entry.summary or "")
            console.print(table)
            console.print("  [dim]install with `agy add <repo>`[/dim]")
    if not query:
        # No filter: also fall back to the local component listing.
        list_components()
    elif not matched:
        console.print(f"[dim]No catalog repos match '{query}'.[/dim]")


@app.command()
def add(
    ref: str = typer.Argument(..., help="Catalog repo (<repo> or <repo>@name[,name]) or full ref <source>/<type>/<name>"),
    type_: list[str] = typer.Option(
        None,
        "--type",
        "-T",
        help="Catalog refs only: install only components of this type "
        "(skill/agent/command/hook/mcp). Repeatable.",
    ),
    path: str = typer.Option(
        None,
        "--path",
        help="Explicit artifact path within the source (e.g. '.' if the repo root is the skill). "
        "Bypasses convention/descriptor discovery.",
    ),
    generate_command: str = typer.Option(
        None,
        "--generate-command",
        help="Self-install via this command instead of linking an artifact "
        "(e.g. 'graphify install --project'). Requires --produces.",
    ),
    generate_setup: list[str] = typer.Option(
        None,
        "--generate-setup",
        help="Command(s) run before --generate-command (e.g. 'uv tool install graphifyy'). Repeatable.",
    ),
    produces: list[str] = typer.Option(
        None,
        "--produces",
        help="Path(s) the generate command creates; agentry tracks + cleans exactly these. Repeatable.",
    ),
    allow_run: bool = _ALLOW_RUN,
) -> None:
    """Enable a component and install it.

    REF is one of: a catalog repo name (``agy add arckit`` — whole repo), a catalog repo with
    selected components (``agy add arckit@code-review,lint``), or a full component ref
    ``<source>/<type>/<name>``. ``--type`` filters a catalog install by component type.
    """
    import shlex

    # A catalog ref never contains '/'; a manual <source>/<type>/<name> ref never contains '@'.
    if "/" not in ref:
        repo, _, names_raw = ref.partition("@")
        names = [n.strip() for n in names_raw.split(",") if n.strip()] if names_raw else []
        _add_from_catalog(repo, names, types=_parse_types(type_), allow_run=allow_run)
        return

    if type_:
        err.print("[red]--type applies only to catalog refs, not a full <source>/<type>/<name> ref.[/red]")
        raise typer.Exit(1)
    source, ctype, name = _parse_ref(ref)
    store = _load()
    if store.parsed().source(source) is None:
        err.print(f"[red]Unknown source '{source}'. Add it first with `agy source add`.[/red]")
        raise typer.Exit(1)

    try:
        generate = None
        if generate_command is not None:
            generate = GeneratorSpec(
                setup=[shlex.split(s) for s in (generate_setup or [])],
                command=shlex.split(generate_command),
                produces=list(produces or []),
            )
        comp = Component(source=source, type=ctype, name=name, enabled=True, path=path, generate=generate)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    store.add_component(comp)
    store.save()
    console.print(f"[green]Added[/green] {ref}")
    _do_sync(allow_run=allow_run)


@app.command()
def remove(ref: str = typer.Argument(..., help="Component ref: <source>/<type>/<name>")) -> None:
    """Remove a component from the config and uninstall it."""
    store = _load()
    if not store.remove_component(ref):
        err.print(f"[yellow]No such component: {ref}[/yellow]")
        raise typer.Exit(1)
    store.save()
    console.print(f"[red]Removed[/red] {ref}")
    _do_sync()


@app.command()
def enable(ref: str = typer.Argument(..., help="Component ref: <source>/<type>/<name>")) -> None:
    """Enable a component (installs it on sync)."""
    _set_enabled(ref, True)


@app.command()
def disable(ref: str = typer.Argument(..., help="Component ref: <source>/<type>/<name>")) -> None:
    """Disable a component (uninstalls it on sync, keeps the config entry)."""
    _set_enabled(ref, False)


def _set_enabled(ref: str, enabled: bool) -> None:
    store = _load()
    if not store.set_enabled(ref, enabled):
        err.print(f"[yellow]No such component: {ref}[/yellow]")
        raise typer.Exit(1)
    store.save()
    console.print(f"[green]{'Enabled' if enabled else 'Disabled'}[/green] {ref}")
    _do_sync()


@app.command(name="sync")
def sync_command(allow_run: bool = _ALLOW_RUN) -> None:
    """Install everything per .agentry.yml + .agentry.lock (idempotent)."""
    _do_sync(allow_run=allow_run)


@app.command(name="install")
def install_command(allow_run: bool = _ALLOW_RUN) -> None:
    """Alias for `sync`."""
    _do_sync(allow_run=allow_run)


@app.command()
def update(
    source: str = typer.Argument(None, help="Only update this source (default: all)."),
) -> None:
    """Re-resolve refs to latest, rewrite .agentry.lock, and reinstall."""
    # `source` is accepted for forward-compat; v1 re-resolves all sources.
    _ = source
    _do_sync(update=True)


@app.command(name="deps")
def deps_cmd() -> None:
    """Show the resolved dependency map (the transitive closure of enabled components)."""
    store = _load()
    config = store.parsed()
    try:
        graph, _ = deps.resolve_graph(_root(), config, load_lock(_root()))
    except (ResolveError, DependencyError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    roots = [c.ref for c in config.components if c.enabled]
    if not roots:
        console.print("[dim]No enabled components — nothing to resolve.[/dim]")
        return

    children: dict[str, list[str]] = {}
    for edge in graph.edges:
        children.setdefault(edge.dependent, []).append(edge.dependency)

    tree = Tree("dependencies")
    path: set[str] = set()

    def add(ref: str, branch: Tree) -> None:
        label = f"[cyan]{ref}[/cyan]" + (" [blue](dep)[/blue]" if ref in graph.transitive else "")
        node = branch.add(label)
        for child in children.get(ref, []):
            if child in path:
                node.add(f"[cyan]{child}[/cyan] [yellow](cycle)[/yellow]")
                continue
            path.add(child)
            add(child, node)
            path.discard(child)

    for r in roots:
        path.add(r)
        add(r, tree)
        path.discard(r)
    console.print(tree)
    for w in graph.warnings:
        err.print(f"[yellow]! {w}[/yellow]")


@app.command(name="status")
def status_cmd() -> None:
    """Show drift between the config and what's installed on disk."""
    try:
        rows, warnings = status(_root())
    except (ResolveError, DependencyError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    table = Table(title="Install status")
    table.add_column("ref", style="cyan")
    table.add_column("target")
    table.add_column("where", style="dim")
    table.add_column("state")
    style = {"ok": "green", "missing": "red", "drift": "yellow"}
    for r in rows:
        table.add_row(r.ref, r.target, r.where, f"[{style.get(r.state, 'white')}]{r.state}[/]")
    if rows:
        console.print(table)
    else:
        console.print("[dim]Nothing installed yet.[/dim]")
    for w in warnings:
        err.print(f"[yellow]! {w}[/yellow]")


# -- source sub-commands -------------------------------------------------


@source_app.command("add")
def source_add(
    name: str = typer.Argument(..., help="Logical name for the source."),
    location: str = typer.Argument(..., help="Git URL, or local path with --local."),
    ref: str = typer.Option("main", "--ref", "-r", help="Git branch/tag/commit."),
    local: bool = typer.Option(False, "--local", help="Treat location as a local directory."),
    subdir: str = typer.Option(
        None, "--subdir", help="Subdirectory within the source where components live (monorepo support)."
    ),
) -> None:
    """Register a source, download it, and sync."""
    store = _load()
    src = (
        Source(name=name, type=SourceType.LOCAL, path=location, subdir=subdir)
        if local
        else Source(name=name, type=SourceType.GIT, url=location, ref=ref, subdir=subdir)
    )
    try:
        store.add_source(src)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    store.save()
    console.print(f"[green]Added source[/green] {name}")
    _do_sync()


@source_app.command("remove")
def source_remove(name: str = typer.Argument(..., help="Source name to remove.")) -> None:
    """Remove a source and uninstall its components."""
    store = _load()
    if not store.remove_source(name):
        err.print(f"[yellow]No such source: {name}[/yellow]")
        raise typer.Exit(1)
    store.save()
    console.print(f"[red]Removed source[/red] {name}")
    _do_sync()


@source_app.command("list")
def source_list() -> None:
    """List configured sources with their locked revision."""
    store = _load()
    config = store.parsed()
    lock = load_lock(_root())
    table = Table(title="Sources")
    table.add_column("name", style="cyan")
    table.add_column("type")
    table.add_column("location", style="dim")
    table.add_column("ref")
    table.add_column("locked")
    for s in config.sources:
        entry = lock.entry(s.name)
        locked = (entry.resolved[:12] if entry else "[dim]—[/dim]")
        table.add_row(s.name, s.type.value, s.url or s.path or "", s.ref if s.type is SourceType.GIT else "—", locked)
    if config.sources:
        console.print(table)
    else:
        console.print("[dim]No sources configured.[/dim]")


# -- repo (catalog) sub-commands -----------------------------------------


@repo_app.command("add")
def repo_add(
    name: str = typer.Argument(..., help="Logical name for the catalog."),
    location: str = typer.Argument(
        ..., help="Catalog file path or http(s) URL (a github.com blob URL works directly)."
    ),
) -> None:
    """Register a repository catalog so `agy add <repo-name>` can resolve a whole repo."""
    from .models import Registry

    store = _load()
    try:
        store.add_repository(Registry(name=name, location=location))
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    store.save()
    console.print(f"[green]Added catalog[/green] {name} → [dim]{location}[/dim]")


@repo_app.command("remove")
def repo_remove(name: str = typer.Argument(..., help="Catalog name to remove.")) -> None:
    """Remove a repository catalog (does not uninstall repos already added from it)."""
    store = _load()
    if not store.remove_repository(name):
        err.print(f"[yellow]No such catalog: {name}[/yellow]")
        raise typer.Exit(1)
    store.save()
    console.print(f"[red]Removed catalog[/red] {name}")


@repo_app.command("list")
def repo_list() -> None:
    """List configured catalogs and the repos they offer."""
    from . import registry as reg

    store = _load()
    config = store.parsed()
    if not config.repositories:
        console.print("[dim]No catalogs configured. Add one with `agy repo add`.[/dim]")
        return
    table = Table(title="Catalogs")
    table.add_column("catalog", style="cyan")
    table.add_column("location", style="dim")
    for r in config.repositories:
        table.add_row(r.name, r.location)
    console.print(table)
    try:
        repos = reg.list_repos(_root(), config)
    except reg.RegistryError as exc:
        err.print(f"[yellow]! {exc}[/yellow]")
        return
    if repos:
        rt = Table(title="Available repositories")
        rt.add_column("repo", style="cyan")
        rt.add_column("catalog", style="dim")
        rt.add_column("components")
        rt.add_column("summary", style="dim")
        for cname, rname, entry in repos:
            scope = f"{len(entry.expose)} curated" if entry.expose else "whole repo"
            rt.add_row(rname, cname, scope, entry.summary or "")
        console.print(rt)


DEFAULT_CATALOG = Path("registry/repositories.json")


@registry_app.command("add")
def registry_add(
    git_url: str = typer.Argument(..., help="Git repo URL (a github.com/owner/repo[/tree/<ref>/<subdir>] URL works)."),
    name: str = typer.Argument(None, help="Repo name in the catalog (default: derived from the URL)."),
    ref: str = typer.Option(None, "--ref", help="Git ref (default: main, or inferred from a /tree/<ref> URL)."),
    subdir: str = typer.Option(None, "--subdir", help="Component subdir within the repo (or inferred from the URL)."),
    summary: str = typer.Option(None, "--summary", help="One-line summary for the entry."),
    discover: bool = typer.Option(False, "--discover", help="Clone the repo and pre-fill `expose` from discovered components."),
    file: Path = typer.Option(DEFAULT_CATALOG, "--file", help="Catalog file to edit."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing entry of the same name."),
) -> None:
    """Add a git/GitHub repo as an entry in a curated catalog (repositories.json)."""
    from . import discovery
    from . import registry as reg
    from .models import ExposeEntry, RegistrySource, RepositoryEntry, Source
    from .resolver import ResolveError, effective_root, resolve

    clean_url, url_ref, url_subdir, default_name = reg.parse_repo_url(git_url)
    name = name or default_name
    ref = ref or url_ref or "main"
    subdir = subdir or url_subdir

    expose: list[ExposeEntry] | None = None
    if discover:
        source = Source(name=name, type=SourceType.GIT, url=clean_url, ref=ref, subdir=subdir)
        try:
            resolve(_root(), source, pinned=None)
            found = discovery.discover(effective_root(_root(), source))
        except (ResolveError, OSError) as exc:
            err.print(f"[red]discover failed: {exc}[/red]")
            raise typer.Exit(1)
        expose = [ExposeEntry(type=d.type, name=d.name) for d in found]
        console.print(f"  [dim]discovered {len(expose)} component(s)[/dim]")

    try:
        entry = RepositoryEntry(
            summary=summary,
            source=RegistrySource(type=SourceType.GIT, url=clean_url, ref=ref, subdir=subdir),
            expose=expose,
        )
        reg.add_entry(file, name, entry, force=force)
    except (reg.RegistryError, ValueError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    scope = f"{len(expose)} curated" if expose else "whole repo"
    console.print(f"[green]Added[/green] {name} → [dim]{file}[/dim] ([cyan]{scope}[/cyan])")


if __name__ == "__main__":
    app()
