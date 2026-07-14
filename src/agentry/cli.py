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
from .config import LOCK_NAME, ConfigStore
from .deps import DependencyError
from .lockfile import load_lock, save_lock
from .models import Component, ComponentType, GeneratorSpec, Source, SourceType, Target
from .reconcile import SyncResult, status, sync
from .resolver import ResolveError, effective_root, resolve
from .targets import BUILTIN_TARGETS, is_builtin

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="agentry (agy) — a dependency manager for AI coding agents.",
)
source_app = typer.Typer(
    no_args_is_help=True, help="Manage component sources (git repos / local dirs)."
)
app.add_typer(source_app, name="source")
catalog_app = typer.Typer(
    no_args_is_help=True, help="Manage catalogs (curated repository indexes)."
)
app.add_typer(catalog_app, name="catalog")
target_app = typer.Typer(
    no_args_is_help=True, help="Manage target driver overlays (how agents install)."
)
app.add_typer(target_app, name="target")
import_app = typer.Typer(
    no_args_is_help=True, help="Import a project from another agent package manager."
)
app.add_typer(import_app, name="import")
emit_app = typer.Typer(
    no_args_is_help=True, help="Emit composed, portable artifacts (e.g. AGENTS.md)."
)
app.add_typer(emit_app, name="emit")

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
        err.print(
            f"[red]Unknown type '{ctype_raw}'. Choose from: {', '.join(t.value for t in ComponentType)}[/red]"
        )
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


def _print_unified_diff(before: str, after: str, label: str) -> None:
    """Show a colored unified diff of a proposed file write (preview before confirm)."""
    import difflib

    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        f"{label} (current)",
        f"{label} (proposed)",
        lineterm="",
    )
    any_line = False
    for line in diff:
        any_line = True
        if line.startswith("+") and not line.startswith("+++"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-") and not line.startswith("---"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(f"[dim]{line}[/dim]")
    if not any_line:
        console.print("[dim](no changes)[/dim]")


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


def _add_from_catalog(
    repo: str, names: list[str], *, types: list[ComponentType], allow_run: bool
) -> None:
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
            "Add one with `agy catalog add <name> <file-or-url>`, or use the full "
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
        err.print(
            f"[red]A different source named '{repo}' already exists; rename or remove it first.[/red]"
        )
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
        console.print(
            "  [dim]added target_profiles from catalog (install overrides for this repo)[/dim]"
        )
    store.save()
    console.print(
        f"[green]Added[/green] {repo} [dim]({len(comps)} component(s) from catalog)[/dim]"
    )
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
        err.print(
            f"[red]Repository '{repo}' has no component{scope} named: {', '.join(sorted(missing))}.[/red]"
        )
        raise typer.Exit(1)
    return selected


def _interactive_pick(available: list[Component]) -> list[Component]:
    """Prompt the user to pick from ``available``; default installs everything."""
    console.print(f"[bold]{len(available)} component(s) available:[/bold]")
    for i, c in enumerate(available, 1):
        console.print(f"  [cyan]{i:>2}[/cyan]  [magenta]{c.type.value}[/magenta]/{c.name}")
    answer = (
        Prompt.ask(
            "Install which? [dim](numbers comma-separated, 'a' for all, or a type name)[/dim]",
            default="a",
        )
        .strip()
        .lower()
    )
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


def _short_sha(sha: str) -> str:
    return sha[len("sha256:") : len("sha256:") + 12] if sha.startswith("sha256:") else sha[:12]


def _interactive_trust(source: str, sha: str) -> bool:
    """Prompt to trust a code-executing source. Declines silently when not a TTY (CI/scripts)."""
    if not sys.stdin.isatty():
        return False
    console.print(
        f"[yellow]Source '{source}' runs code at install (a generator)[/yellow], pinned at "
        f"[cyan]{_short_sha(sha)}[/cyan]."
    )
    return typer.confirm(f"Trust '{source}' to execute its installer?", default=False)


def _do_sync(
    *,
    update: bool = False,
    allow_run: bool = False,
    frozen: bool = False,
    allow_transform: bool = False,
) -> None:
    try:
        res = sync(
            _root(),
            update=update,
            allow_run=allow_run,
            frozen=frozen,
            allow_transform=allow_transform,
            trust_callback=_interactive_trust,
        )
    except (ResolveError, DependencyError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    _print_result(res)


_ALLOW_RUN = typer.Option(
    False,
    "--allow-run",
    help="Permit components with a 'generate' spec to run their own installer commands.",
)

_FROZEN = typer.Option(
    False,
    "--frozen",
    help="Install strictly from .agentry.lock; fail if any source is unpinned or has drifted.",
)

_ALLOW_TRANSFORM = typer.Option(
    False,
    "--allow-transform",
    help="Permit components with an 'agent' transform to run the configured agent command.",
)


# -- top-level commands --------------------------------------------------


@app.command()
def version() -> None:
    """Print the agentry version."""
    console.print(f"agentry {__version__}")


@app.command()
def init(
    target: list[str] = typer.Option(
        None,
        "--target",
        "-t",
        help="Target AI tool(s): claude, opencode, cursor, codex, gemini, windsurf, kimi "
        "(or a custom tool defined under target_profiles). Repeatable.",
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
    ref: str = typer.Argument(
        ..., help="Catalog repo (<repo> or <repo>@name[,name]) or full ref <source>/<type>/<name>"
    ),
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
        err.print(
            "[red]--type applies only to catalog refs, not a full <source>/<type>/<name> ref.[/red]"
        )
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
        comp = Component(
            source=source, type=ctype, name=name, enabled=True, path=path, generate=generate
        )
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
def sync_command(
    allow_run: bool = _ALLOW_RUN, frozen: bool = _FROZEN, allow_transform: bool = _ALLOW_TRANSFORM
) -> None:
    """Install everything per .agentry.yml + .agentry.lock (idempotent)."""
    _do_sync(allow_run=allow_run, frozen=frozen, allow_transform=allow_transform)


@app.command(name="install")
def install_command(
    allow_run: bool = _ALLOW_RUN, frozen: bool = _FROZEN, allow_transform: bool = _ALLOW_TRANSFORM
) -> None:
    """Alias for `sync`."""
    _do_sync(allow_run=allow_run, frozen=frozen, allow_transform=allow_transform)


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


@app.command(name="doctor")
def doctor_cmd(
    strict: bool = typer.Option(
        False, "--strict", help="Treat warnings as failures too (exit 1 on any issue; for CI)."
    ),
) -> None:
    """Preflight the project: surface undefined targets, unprovided components, unset ${VARs},
    unsupported type/target combos, and drift — loudly, before they bite.

    Exits 1 if any **error** is found (or any warning under `--strict`); 0 when clean.
    """
    from .doctor import run_checks

    try:
        checks = run_checks(_root())
    except FileNotFoundError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except (ResolveError, DependencyError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    mark = {"error": "[red]✗[/red]", "warn": "[yellow]![/yellow]", "ok": "[green]✓[/green]"}
    for c in checks:
        out = err if c.level == "error" else console
        out.print(f"  {mark.get(c.level, '?')} [dim]{c.category}[/dim]  {c.message}")
    errors = sum(c.level == "error" for c in checks)
    warns = sum(c.level == "warn" for c in checks)
    if errors:
        err.print(f"[red]doctor: {errors} error(s), {warns} warning(s).[/red]")
        raise typer.Exit(1)
    if warns:
        msg = f"doctor: 0 errors, {warns} warning(s)."
        if strict:
            err.print(f"[red]{msg} (--strict)[/red]")
            raise typer.Exit(1)
        console.print(f"[yellow]{msg}[/yellow]")
        return
    console.print("[green]doctor: all checks passed.[/green]")


def _source_provenance(store: ConfigStore, source_name: str) -> str:
    """One-line provenance for a source: where it came from and the pinned revision."""
    lock = load_lock(_root())
    entry = lock.entry(source_name)
    src = next((s for s in store.parsed().sources if s.name == source_name), None)
    if entry is None:
        return f"{source_name} [dim](unresolved — run `agy sync`)[/dim]"
    resolved = entry.resolved
    short = (
        resolved[len("sha256:") : len("sha256:") + 12]
        if resolved.startswith("sha256:")
        else resolved[:12]
    )
    if entry.type is SourceType.GIT:
        where = entry.url or (src.url if src else "?")
        return f"{source_name} [dim]git[/dim] {where} @ [cyan]{short}[/cyan] (ref {entry.ref or (src.ref if src else '?')})"
    where = entry.path or (src.path if src else "?")
    return f"{source_name} [dim]local[/dim] {where} @ [cyan]{short}[/cyan]"


@app.command(name="why")
def why_cmd(ref: str = typer.Argument(..., help="Component ref: <source>/<type>/<name>.")) -> None:
    """Explain a component: where it came from (source + pinned revision) and where it installs.

    Provenance with no guessing — the counter to silent target autodetection.
    """
    store = _load()
    component = store.parsed().find_component(ref)
    if component is None:
        err.print(f"[red]No such component in config: {ref}[/red]")
        raise typer.Exit(1)
    try:
        rows, warnings = status(_root())
    except (ResolveError, DependencyError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    tree = Tree(
        f"[cyan]{ref}[/cyan]" + ("" if component.enabled else " [yellow](disabled)[/yellow]")
    )
    tree.add(f"source: {_source_provenance(store, component.source)}")
    targets = tree.add("installs to")
    style = {"ok": "green", "missing": "red", "drift": "yellow"}
    mine = [r for r in rows if r.ref == ref]
    if mine:
        for r in mine:
            targets.add(
                f"[bold]{r.target}[/bold]  [dim]{r.where}[/dim]  [{style.get(r.state, 'white')}]{r.state}[/]"
            )
    else:
        targets.add("[dim]no target resolves this component[/dim]")
    console.print(tree)
    for w in warnings:
        err.print(f"[yellow]! {w}[/yellow]")


@app.command(name="trust")
def trust_cmd(
    source: str = typer.Argument(..., help="Source name to trust for install-time code execution."),
) -> None:
    """Record consent for a source to run code at install (generators).

    The decision is pinned to the source's resolved SHA in ``.agentry.lock`` — if the source
    later moves to a new revision, trust is dropped and must be re-confirmed.
    """
    root = _root()
    lock = load_lock(root)
    entry = lock.entry(source)
    if entry is None:
        err.print(
            f"[red]No resolved source '{source}' in {LOCK_NAME} — run `agy sync` first.[/red]"
        )
        raise typer.Exit(1)
    if entry.trusted:
        console.print(
            f"[green]Source '{source}' is already trusted[/green] @ "
            f"[cyan]{_short_sha(entry.resolved)}[/cyan]"
        )
        return
    entry.trusted = True
    save_lock(root, lock)
    console.print(
        f"[green]Trusted source[/green] {source} @ [cyan]{_short_sha(entry.resolved)}[/cyan]"
    )


# -- source sub-commands -------------------------------------------------


@source_app.command("add")
def source_add(
    name: str = typer.Argument(..., help="Logical name for the source."),
    location: str = typer.Argument(..., help="Git URL, or local path with --local."),
    ref: str = typer.Option("main", "--ref", "-r", help="Git branch/tag/commit."),
    local: bool = typer.Option(False, "--local", help="Treat location as a local directory."),
    subdir: str = typer.Option(
        None,
        "--subdir",
        help="Subdirectory within the source where components live (monorepo support).",
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
    # Provenance at first install: show exactly what was pinned (URL/path + resolved SHA),
    # so a new source's origin is visible up front rather than only via a later `agy why`.
    console.print(f"  [dim]provenance:[/dim] {_source_provenance(_load(), name)}")


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
        locked = entry.resolved[:12] if entry else "[dim]—[/dim]"
        table.add_row(
            s.name,
            s.type.value,
            s.url or s.path or "",
            s.ref if s.type is SourceType.GIT else "—",
            locked,
        )
    if config.sources:
        console.print(table)
    else:
        console.print("[dim]No sources configured.[/dim]")


# -- catalog sub-commands ------------------------------------------------


@catalog_app.command("add")
def catalog_add(
    name: str = typer.Argument(..., help="Logical name for the catalog."),
    location: str = typer.Argument(
        ..., help="Catalog file path or http(s) URL (a github.com blob URL works directly)."
    ),
) -> None:
    """Register a catalog so `agy add <repo-name>` can resolve a whole repo."""
    from .models import Registry

    store = _load()
    try:
        store.add_repository(Registry(name=name, location=location))
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    store.save()
    console.print(f"[green]Added catalog[/green] {name} → [dim]{location}[/dim]")


@catalog_app.command("remove")
def catalog_remove(name: str = typer.Argument(..., help="Catalog name to remove.")) -> None:
    """Remove a catalog (does not uninstall repos already added from it)."""
    store = _load()
    if not store.remove_repository(name):
        err.print(f"[yellow]No such catalog: {name}[/yellow]")
        raise typer.Exit(1)
    store.save()
    console.print(f"[red]Removed catalog[/red] {name}")


@catalog_app.command("list")
def catalog_list() -> None:
    """List configured catalogs and the repos they offer."""
    from . import registry as reg

    store = _load()
    config = store.parsed()
    if not config.repositories:
        console.print("[dim]No catalogs configured. Add one with `agy catalog add`.[/dim]")
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


@catalog_app.command("add-repo")
def catalog_add_repo(
    git_url: str = typer.Argument(
        ...,
        help="Git repo URL (a github.com/owner/repo or .../tree/<ref>/<subdir> URL works).",
    ),
    name: str = typer.Argument(
        None, help="Repo name in the catalog (default: derived from the URL)."
    ),
    ref: str = typer.Option(
        None, "--ref", help="Git ref (default: main, or inferred from a /tree/<ref> URL)."
    ),
    subdir: str = typer.Option(
        None, "--subdir", help="Component subdir within the repo (or inferred from the URL)."
    ),
    summary: str = typer.Option(None, "--summary", help="One-line summary for the entry."),
    discover: bool = typer.Option(
        False, "--discover", help="Clone the repo and pre-fill `expose` from discovered components."
    ),
    file: Path = typer.Option(DEFAULT_CATALOG, "--file", help="Catalog file to edit."),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing entry of the same name."
    ),
) -> None:
    """Add a git/GitHub repo as an entry in a catalog file (registry/repositories.json).

    Authors a catalog (the inverse of `catalog add`, which registers a catalog to consume).
    """
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


# -- target sub-commands -------------------------------------------------


@target_app.command("add")
def target_add(
    name: str = typer.Argument(..., help="Driver-overlay name published by a catalog."),
    catalog: str = typer.Option(None, "--catalog", "-c", help="Restrict to this catalog."),
) -> None:
    """Install a shared driver overlay into `.agentry.yml` `target_profiles`, then sync.

    A catalog can publish *driver overlays* — named definitions of how some agent installs
    each component type. Installing one makes that target resolvable without hand-writing
    `target_profiles`: the community-driver layer. Universality you don't have to author.
    """
    from . import registry as reg

    store = _load()
    config = store.parsed()
    if not config.repositories:
        err.print(
            "[red]No catalogs configured.[/red] Add one with `agy catalog add <name> <file-or-url>`."
        )
        raise typer.Exit(1)
    try:
        match = reg.find_target(_root(), config, name, catalog=catalog)
    except reg.RegistryError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if match is None:
        where = f" in catalog '{catalog}'" if catalog else ""
        err.print(
            f"[red]No driver overlay named '{name}'{where}.[/red] "
            "Run `agy target list` to see what's available."
        )
        raise typer.Exit(1)
    registry, profile = match
    if store.merge_target_profiles({name: profile}):
        store.save()
        console.print(
            f"[green]Added[/green] driver overlay [cyan]{name}[/cyan] [dim](from {registry.name})[/dim]"
        )
    else:
        console.print(
            f"[dim]Target '{name}' already has profile rules in .agentry.yml — left as-is.[/dim]"
        )
    _do_sync()


@target_app.command("list")
def target_list() -> None:
    """Show targets in use, whether each resolves, and installable overlays from catalogs."""
    from . import registry as reg

    store = _load()
    config = store.parsed()
    active = config.active_targets()
    available: dict[str, str] = {}
    try:
        for cat, tname, _ in reg.list_targets(_root(), config):
            available.setdefault(tname, cat)
    except reg.RegistryError as exc:
        err.print(f"[yellow]! {exc}[/yellow]")

    table = Table(title="Targets in use")
    table.add_column("target", style="cyan")
    table.add_column("status")
    table.add_column("source", style="dim")
    for t in sorted(active):
        if is_builtin(t):
            table.add_row(t, "[green]resolved[/green]", "built-in")
        elif t in config.target_profiles:
            table.add_row(t, "[green]resolved[/green]", ".agentry.yml profile")
        elif t in available:
            table.add_row(
                t,
                "[yellow]overlay available[/yellow]",
                f"`agy target add {t}` (from {available[t]})",
            )
        else:
            table.add_row(t, "[red]unresolved[/red]", "no built-in, profile, or overlay")
    if active:
        console.print(table)
    else:
        console.print("[dim]No targets configured.[/dim]")
    extras = sorted((t, c) for t, c in available.items() if t not in active)
    if extras:
        console.print(
            "\n[dim]Other installable overlays:[/dim] "
            + ", ".join(f"[cyan]{t}[/cyan] [dim]({c})[/dim]" for t, c in extras)
        )


# -- import sub-commands -------------------------------------------------


@import_app.command("apm")
def import_apm(
    file: Path = typer.Option(Path("apm.yml"), "--file", "-f", help="Path to the apm.yml."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be imported; write nothing."
    ),
) -> None:
    """Translate a Microsoft apm project (`apm.yml`) into `.agentry.yml`.

    Maps apm dependencies to agentry sources + components, inline MCP servers to MCP fragments,
    and apm targets to agentry targets. Run `agy sync` afterwards to install. Anything that
    can't be inferred offline is reported as a warning pointing at `agy add` / `agy list`.
    """
    import json as _json

    from ruamel.yaml import YAML

    from .apm_import import translate_apm

    path = file if file.is_absolute() else _root() / file
    if not path.is_file():
        err.print(f"[red]No apm manifest at {path}.[/red] Point at one with `--file`.")
        raise typer.Exit(1)
    try:
        doc = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — surface any parse error as a clean CLI message
        err.print(f"[red]Could not parse {path}: {exc}[/red]")
        raise typer.Exit(1)

    result = translate_apm(doc)
    mcp_source = "apm-import"

    # Summary of the planned import.
    console.print(f"[bold]Importing[/bold] {path}")
    for s in result.sources:
        where = s.url or s.path
        console.print(f"  [green]source[/green] {s.name} [dim]({s.type.value} {where})[/dim]")
    for c in result.components:
        console.print(f"  [green]component[/green] {c.ref}")
    for name in result.mcp_fragments:
        console.print(f"  [green]mcp[/green] {name} [dim](→ {mcp_source}/mcp/{name}.json)[/dim]")
    for w in result.warnings:
        err.print(f"  [yellow]! {w}[/yellow]")

    if dry_run:
        console.print("[dim]--dry-run: nothing written.[/dim]")
        return
    if not (result.sources or result.components or result.mcp_fragments):
        console.print("[dim]Nothing to import.[/dim]")
        return

    # Materialize inline MCP servers as a committed local source of fragments.
    if result.mcp_fragments:
        mcp_dir = _root() / mcp_source / "mcp"
        mcp_dir.mkdir(parents=True, exist_ok=True)
        for name, frag in result.mcp_fragments.items():
            (mcp_dir / f"{name}.json").write_text(
                _json.dumps(frag, indent=2) + "\n", encoding="utf-8"
            )
        result.sources.append(Source(name=mcp_source, type=SourceType.LOCAL, path=mcp_source))
        for name in result.mcp_fragments:
            result.components.append(
                Component(source=mcp_source, type=ComponentType.MCP, name=name)
            )

    if ConfigStore.exists(_root()):
        store = ConfigStore.load(_root())
        if result.targets and set(result.targets) - set(store.parsed().targets):
            err.print(
                "  [yellow]! apm targets "
                f"{result.targets} not added — edit `targets` in .agentry.yml if you want them[/yellow]"
            )
    else:
        store = ConfigStore.create(_root(), result.targets or [Target.CLAUDE])
    for s in result.sources:
        store.add_source(s)
    for c in result.components:
        store.add_component(c)
    store.save()

    console.print(
        f"[green]Imported[/green] {len(result.sources)} source(s), "
        f"{len(result.components)} component(s) into .agentry.yml. "
        "Review it, then run [bold]agy sync[/bold]."
    )


# -- emit sub-commands ---------------------------------------------------


@emit_app.command("agents-md")
def emit_agents_md(
    output: Path = typer.Option(Path("AGENTS.md"), "--output", "-o", help="File to write."),
    check: bool = typer.Option(
        False, "--check", help="Verify the file is up to date; exit 1 if not (for CI). No write."
    ),
    agent: bool = typer.Option(
        False, "--agent", help="Synthesize via your configured agent CLI instead of concatenating."
    ),
    allow_transform: bool = typer.Option(
        False, "--allow-transform", help="Permit --agent to run the configured agent command."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt (auto-apply; for CI)."
    ),
) -> None:
    """Compose a portable AGENTS.md from this project's skills/agents/commands.

    Default (deterministic) mode concatenates component bodies — same inputs produce the same
    bytes, so the result is safe to commit and verify in CI with `--check`. With `--agent`, the
    content is instead *synthesized* by your own agent CLI (configured under `transform.command`
    in .agentry.yml); that runs only with `--allow-transform`, previews a diff, and asks before
    writing (skip the prompt with `--yes`).
    """
    from .emit import (
        TransformError,
        build_synthesis_prompt,
        compose_agents_md,
        gather_items,
        run_agent,
    )

    store = _load()
    config = store.parsed()
    items = gather_items(_root(), config)
    if not items:
        console.print(
            "[yellow]No skill/agent/command components to compose into AGENTS.md.[/yellow]"
        )
        raise typer.Exit(0)
    target = output if output.is_absolute() else _root() / output

    if agent:
        if check:
            err.print(
                "[red]--check is for the deterministic mode; agent output isn't reproducible.[/red]"
            )
            raise typer.Exit(1)
        command = config.transform.command if config.transform else []
        if not command:
            err.print(
                "[red]No transform command configured.[/red] Set `transform.command` "
                "(e.g. [claude, -p]) in .agentry.yml."
            )
            raise typer.Exit(1)
        if not allow_transform:
            err.print(
                "[red]--agent needs --allow-transform[/red] (it runs your configured agent "
                f"command: [dim]{' '.join(command)}[/dim])."
            )
            raise typer.Exit(1)
        console.print(f"  [dim]synthesizing via[/dim] {' '.join(command)} …")
        try:
            content = run_agent(command, build_synthesis_prompt(items))
        except TransformError as exc:
            err.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current == content:
            console.print(f"[green]{output} already matches the synthesized output.[/green]")
            return
        _print_unified_diff(current, content, str(output))
        if not yes and not typer.confirm(f"Write {output}?"):
            console.print("[dim]Aborted; nothing written.[/dim]")
            raise typer.Exit(0)
    else:
        content = compose_agents_md(items)
        if check:
            current = target.read_text(encoding="utf-8") if target.is_file() else ""
            if current != content:
                err.print(
                    f"[red]{output} is out of date.[/red] Run `agy emit agents-md` to refresh."
                )
                raise typer.Exit(1)
            console.print(f"[green]{output} is up to date[/green] ({len(items)} component(s)).")
            return

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    console.print(f"[green]Wrote[/green] {output} [dim]from {len(items)} component(s)[/dim].")


def _trigger_memory_files(config, root: Path) -> list[Path]:
    """Resolved memory-file paths (deduped, sorted) for every active target that declares one."""
    from .drivers import resolve_drivers

    drivers = resolve_drivers(config)
    seen: dict[Path, None] = {}
    for tname in sorted(config.active_targets()):
        driver = drivers.get(tname)
        mem = driver.spec.memory_file if driver else None
        if not mem:
            continue
        path = root / mem
        seen.setdefault(path, None)
    return list(seen)


@emit_app.command("triggers")
def emit_triggers(
    check: bool = typer.Option(
        False, "--check", help="Verify memory files are up to date; exit 1 if not (for CI)."
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write to this single file instead of fanning out to every target's memory file.",
    ),
) -> None:
    """Register a skill-trigger block into each target's memory file (CLAUDE.md, AGENTS.md, …).

    Composes one bullet per installed skill — its name mapped to its SKILL.md ``description``
    (the "use when …" trigger) — and splices it, between managed markers, into every active
    target's always-loaded instruction file. Only the marker-delimited block is written, so
    the rest of a hand-authored memory file is left untouched; refresh is idempotent. Use
    ``--check`` in CI, or ``-o PATH`` to target a single file.
    """
    from .emit import compose_triggers_block, gather_items, merge_managed_block

    config = _load().parsed()
    root = _root()
    items = [i for i in gather_items(root, config) if i.type is ComponentType.SKILL]
    if not items:
        console.print("[yellow]No skill components to register as triggers.[/yellow]")
        raise typer.Exit(0)

    block = compose_triggers_block(items)

    if output is not None:
        targets = [output if output.is_absolute() else root / output]
    else:
        targets = _trigger_memory_files(config, root)
        if not targets:
            console.print(
                "[yellow]No active target declares a memory file; nothing to register.[/yellow]"
            )
            raise typer.Exit(0)

    stale: list[Path] = []
    for path in targets:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        try:
            desired = merge_managed_block(current, block)
        except ValueError as exc:
            err.print(f"[red]{path}: {exc}[/red]")
            raise typer.Exit(1)
        rel = path.relative_to(root) if path.is_relative_to(root) else path
        if check:
            if current != desired:
                stale.append(rel)
            continue
        if current == desired:
            console.print(f"[dim]{rel} already up to date.[/dim]")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desired, encoding="utf-8")
        console.print(f"[green]Registered[/green] {rel} [dim]({len(items)} skill(s))[/dim].")

    if check:
        if stale:
            listed = ", ".join(str(p) for p in stale)
            err.print(f"[red]Out of date:[/red] {listed}. Run `agy emit triggers` to refresh.")
            raise typer.Exit(1)
        console.print(f"[green]Triggers up to date[/green] ({len(items)} skill(s)).")


if __name__ == "__main__":
    app()
