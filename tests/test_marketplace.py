"""MP1 / ADR-002: a Claude Code plugin marketplace is an agentry catalog.

`catalog add NAME <marketplace.json | marketplace root | GitHub URL>` lists its plugins;
`add <plugin>` installs one through T25 discovery, pinned by SHA in the lock, fully reversible.
Hermetic: marketplaces are local dirs/files, installable plugins are local `file://` git repos,
and GitHub URLs are served by a fake `urlopen` (no network).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentry import registry as reg
from agentry.config import ConfigStore
from agentry.lockfile import load_lock, lock_path
from agentry.models import Registry

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@e.x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e.x",
}
_CLI_ENV = {"COLUMNS": "250"}


# --- helpers ------------------------------------------------------------------------------


def _write(root: Path, rel: str, text: str = "x\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, env=_GIT_ENV, capture_output=True, text=True
    ).stdout.strip()


def _commit_all(repo: Path, msg: str = "c") -> str:
    if not (repo / ".git").exists():
        _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _plugin_repo(tmp_path: Path, name: str, files: dict[str, str]) -> tuple[Path, str, str]:
    """A plugin as its own file:// git repo. Returns (repo, url, head sha)."""
    repo = tmp_path / f"plugin-{name}"
    for rel, text in files.items():
        _write(repo, rel, text)
    sha = _commit_all(repo)
    return repo, f"file://{repo}", sha


def _marketplace(root: Path, plugins: list[dict], **extra) -> Path:
    doc = {"name": "mk", "owner": {"name": "o"}, "plugins": plugins, **extra}
    return _write(root, ".claude-plugin/marketplace.json", json.dumps(doc))


def _url_entry(name: str, url: str, sha: str | None = None, **kw) -> dict:
    src = {"source": "url", "url": url, **({"sha": sha} if sha else {})}
    return {"name": name, "source": src, "description": f"{name} plugin", **kw}


def _project(tmp_path: Path, targets=("claude",)) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    ConfigStore.create(proj, list(targets)).save()
    return proj


def _cli(proj: Path, monkeypatch, *args: str):
    monkeypatch.chdir(proj)
    return CliRunner(env=_CLI_ENV).invoke(__import__("agentry.cli").cli.app, list(args))


def _text(result) -> str:
    """Everything the command printed, wrap-insensitive. Click's `output` already interleaves
    stderr, so stderr is not appended again (that would double-count every warning)."""
    return " ".join(result.output.split())


def _catalog(proj: Path, location: str | Path):
    return reg.load_catalog(proj, Registry(name="mk", location=str(location)))


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, url: str):
        super().__init__(body)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, body: bytes) -> list[str]:
    """Fake urlopen: serve `body` for any URL, recording what was requested."""
    requested: list[str] = []

    def fake(req, timeout=None):
        url = getattr(req, "full_url", req)
        requested.append(url)
        return _Resp(body, url)

    monkeypatch.setattr(reg.urllib.request, "urlopen", fake)
    return requested


# --- format detection and translation -------------------------------------------------------


def test_catalog_add_marketplace_json_lists_plugins(tmp_path: Path, monkeypatch):
    mk = tmp_path / "mk"
    path = _marketplace(
        mk,
        [
            {
                "name": "alpha",
                "source": "./plugins/alpha",
                "description": "A tools",
                "version": "1.2.0",
            },
            {"name": "beta", "source": "./plugins/beta", "description": "B tools"},
        ],
    )
    _write(mk, "plugins/alpha/commands/a.md")
    _write(mk, "plugins/beta/commands/b.md")
    proj = _project(tmp_path)
    assert _cli(proj, monkeypatch, "catalog", "add", "mk", str(path)).exit_code == 0

    index = _catalog(proj, path)
    assert set(index.repositories) == {"alpha", "beta"}
    assert index.repositories["alpha"].summary == "A tools (v1.2.0)"  # version is a label only
    search = _text(_cli(proj, monkeypatch, "search"))
    assert "alpha" in search and "A tools" in search and "beta" in search


def test_catalog_add_marketplace_root_dir_and_github_url(tmp_path: Path, monkeypatch):
    mk = tmp_path / "mk"
    _marketplace(mk, [{"name": "alpha", "source": "./plugins/alpha"}])
    proj = _project(tmp_path)
    # A local marketplace *root* resolves to <root>/.claude-plugin/marketplace.json.
    assert set(_catalog(proj, mk).repositories) == {"alpha"}

    # A GitHub repo URL resolves to the repo's marketplace.json (served by the fake).
    body = (mk / ".claude-plugin" / "marketplace.json").read_bytes()
    requested = _serve(monkeypatch, body)
    index = reg.load_catalog(proj, Registry(name="gh", location="https://github.com/o/r"))
    assert set(index.repositories) == {"alpha"}
    assert requested and requested[-1].endswith(".claude-plugin/marketplace.json")


def test_marketplace_translates_relative_github_url_gitsubdir(tmp_path: Path, monkeypatch):
    sha = "a" * 40
    doc = {
        "name": "mk",
        "owner": {"name": "o"},
        "metadata": {"pluginRoot": "./plugins"},
        "plugins": [
            {"name": "rel", "source": "./plugins/rel"},
            {"name": "bare", "source": "bare"},  # under metadata.pluginRoot
            {"name": "gh", "source": {"source": "github", "repo": "x/y", "ref": "v1", "sha": sha}},
            {
                "name": "u",
                "source": {"source": "url", "url": "https://e.invalid/z.git", "ref": "dev"},
            },
            {"name": "sub", "source": {"source": "git-subdir", "url": "x/y", "path": "p/q"}},
        ],
    }
    _serve(monkeypatch, json.dumps(doc).encode())
    proj = _project(tmp_path)
    repos = reg.load_catalog(
        proj, Registry(name="mk", location="https://github.com/o/r")
    ).repositories

    def src(name):
        s = repos[name].source
        return (s.type.value, s.url, s.ref, s.subdir)

    assert src("rel")[1] == "https://github.com/o/r" and src("rel")[3] == "plugins/rel"
    assert src("bare")[1] == "https://github.com/o/r" and src("bare")[3] == "plugins/bare"
    assert src("gh") == ("git", "https://github.com/x/y", sha, None)  # sha beats ref
    assert src("u") == ("git", "https://e.invalid/z.git", "dev", None)
    assert src("sub")[:2] == ("git", "https://github.com/x/y") and src("sub")[3] == "p/q"


def test_marketplace_skips_npm_archive_command_with_one_warning_each(tmp_path: Path, monkeypatch):
    mk = tmp_path / "mk"
    path = _marketplace(
        mk,
        [
            {"name": "n", "source": {"source": "npm", "package": "@x/y"}},
            {"name": "a", "source": {"source": "archive", "url": "https://e.invalid/a.tgz"}},
            {"name": "c", "source": {"source": "command", "command": "curl … | sh"}},
            {"name": "ok", "source": "./plugins/ok"},
        ],
    )
    _write(mk, "plugins/ok/commands/ok.md")
    proj = _project(tmp_path)
    out = _text(_cli(proj, monkeypatch, "catalog", "add", "mk", str(path)))
    for p, kind in (("n", "an npm"), ("a", "an archive"), ("c", "a command")):
        line = (
            f"catalog 'mk': plugin '{p}' uses {kind} source, which agentry can't install — "
            "skipped. Install it in Claude Code directly."
        )
        assert out.count(line) == 1
    assert set(_catalog(proj, path).repositories) == {"ok"}


def test_relative_entry_from_bare_url_skipped(tmp_path: Path, monkeypatch):
    doc = {"name": "mk", "owner": {"name": "o"}, "plugins": [{"name": "rel", "source": "./p/rel"}]}
    _serve(monkeypatch, json.dumps(doc).encode())
    proj = _project(tmp_path)
    res = _cli(
        proj, monkeypatch, "catalog", "add", "mk", "https://example.invalid/marketplace.json"
    )
    assert (
        "catalog 'mk': plugin 'rel' uses a relative path, but the marketplace was added from a "
        "URL with no repo — skipped. Add the marketplace by its repo URL or a local checkout."
    ) in _text(res)


def test_bad_entry_does_not_fail_catalog(tmp_path: Path, monkeypatch):
    mk = tmp_path / "mk"
    path = _marketplace(
        mk,
        [
            {"name": "broken", "source": {"source": "github"}},
            {"name": "ok", "source": "./plugins/ok"},
        ],
    )
    _write(mk, "plugins/ok/commands/ok.md")
    proj = _project(tmp_path)
    res = _cli(proj, monkeypatch, "catalog", "add", "mk", str(path))
    assert res.exit_code == 0
    assert "broken" in _text(res)  # warned about
    assert set(_catalog(proj, path).repositories) == {"ok"}  # the catalog still loads


_ONE_FIELD = (
    "catalog 'mk': plugin 'p' defines `commands` in marketplace.json; agentry reads only the "
    "plugin's own files and plugin.json — it is not installed. Ask the author to move it into "
    "plugin.json."
)
_TWO_FIELDS = (
    "catalog 'mk': plugin 'p' defines `commands` and `hooks` in marketplace.json; agentry reads "
    "only the plugin's own files and plugin.json — those are not installed. Ask the author to "
    "move them into plugin.json."
)
_ONE_META = "catalog 'mk': plugin 'p': `dependencies` is a Claude-only setting — ignored."
_TWO_META = (
    "catalog 'mk': plugin 'p': `dependencies` and `defaultEnabled` are Claude-only settings — "
    "ignored."
)


@pytest.mark.parametrize(
    ("fields", "meta", "want_fields", "want_meta"),
    [
        ({"commands": ["./extra"]}, {"dependencies": ["o"]}, _ONE_FIELD, _ONE_META),
        (
            {"commands": ["./extra"], "hooks": "./h.json"},
            {"dependencies": ["o"], "defaultEnabled": True},
            _TWO_FIELDS,
            _TWO_META,
        ),
    ],
    ids=["singular", "plural"],
)
def test_entry_component_fields_ignored_with_warning(
    tmp_path: Path, monkeypatch, fields, meta, want_fields, want_meta
):
    mk = tmp_path / "mk"
    path = _marketplace(mk, [{"name": "p", "source": "./plugins/p", **fields, **meta}])
    _write(mk, "plugins/p/commands/c.md")
    proj = _project(tmp_path)
    out = _text(_cli(proj, monkeypatch, "catalog", "add", "mk", str(path)))
    assert out.count(want_fields) == 1
    assert out.count(want_meta) == 1
    other = {_ONE_FIELD, _TWO_FIELDS, _ONE_META, _TWO_META} - {want_fields, want_meta}
    assert not [line for line in other if line in out]  # never both number forms


# --- install and removal ------------------------------------------------------------------


def _install_fixture(tmp_path: Path, monkeypatch, files: dict[str, str], targets=("claude",)):
    """A marketplace listing one SHA-pinned file:// plugin; the project has the catalog."""
    repo, url, sha = _plugin_repo(tmp_path, "tools", files)
    mk = tmp_path / "mk"
    path = _marketplace(mk, [_url_entry("tools", url, sha, version="9.9.9")])
    proj = _project(tmp_path, targets)
    assert _cli(proj, monkeypatch, "catalog", "add", "mk", str(path)).exit_code == 0
    return proj, repo, url, sha, path


def _snapshot(proj: Path) -> dict[str, bytes | str]:
    out: dict[str, bytes | str] = {}
    for p in sorted(proj.rglob("*")):
        rel = p.relative_to(proj).as_posix()
        if rel.startswith(".agentry") or rel in (".agentry.yml", ".gitignore"):
            continue
        out[rel] = (
            ("->" + os.readlink(p))
            if p.is_symlink()
            else (p.read_bytes() if p.is_file() else "dir")
        )
    return out


def test_add_plugin_installs_all_targets_and_pins_sha(tmp_path: Path, monkeypatch):
    proj, _, url, sha, _ = _install_fixture(
        tmp_path, monkeypatch, {"commands/review.md": "# review\n"}, targets=("claude", "opencode")
    )
    res = _cli(proj, monkeypatch, "add", "tools")
    assert res.exit_code == 0, res.output
    assert (proj / ".claude/commands/tools/review.md").read_text("utf-8") == "# review\n"
    assert (proj / ".opencode/commands/review.md").read_text("utf-8") == "# review\n"

    entry = load_lock(proj).entry("tools")
    assert entry.resolved == sha
    (source,) = [s for s in ConfigStore.load(proj).parsed().sources if s.name == "tools"]
    assert source.url == url and source.ref == sha
    assert "9.9.9" not in (proj / ".agentry.yml").read_text("utf-8")  # version: label only
    assert "9.9.9" not in lock_path(proj).read_text("utf-8")


def test_remove_plugin_fully_reversible(tmp_path: Path, monkeypatch):
    proj, *_ = _install_fixture(
        tmp_path,
        monkeypatch,
        {
            "commands/review.md": "# r\n",
            ".claude-plugin/plugin.json": json.dumps(
                {"name": "tools", "mcpServers": {"srv": {"command": "x"}}}
            ),
        },
        targets=("claude", "opencode"),
    )
    _write(proj, ".mcp.json", json.dumps({"mcpServers": {"mine": {"command": "USER"}}}))
    before = _snapshot(proj)
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    assert _snapshot(proj) != before
    for comp in [c for c in ConfigStore.load(proj).parsed().components if c.source == "tools"]:
        assert _cli(proj, monkeypatch, "remove", comp.ref).exit_code == 0
    after = _snapshot(proj)
    # Merge removal is key-scoped, not byte-scoped (as for every merge since ADR-001): the
    # user's config files are compared parsed. A config file agentry itself created (here
    # opencode.json) and the folders it created are deleted once emptied (#45): nothing new
    # is left behind.
    assert set(after) == set(before), sorted(set(after) ^ set(before))
    user_mcp = json.loads(after[".mcp.json"])["mcpServers"]
    assert user_mcp == {"mine": {"command": "USER"}}  # the user's entry survives, ours is gone
    for rel, data in before.items():
        if rel.endswith(".json"):
            assert json.loads(after[rel]) == json.loads(data), rel  # the user's .mcp.json
        else:
            assert after[rel] == data, rel


def test_sha_pinned_plugin_not_moved_by_update(tmp_path: Path, monkeypatch):
    proj, repo, _, sha, _ = _install_fixture(tmp_path, monkeypatch, {"commands/review.md": "v1\n"})
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    _write(repo, "commands/review.md", "v2\n")
    _commit_all(repo, "v2")

    assert _cli(proj, monkeypatch, "update", "tools").exit_code == 0
    assert load_lock(proj).entry("tools").resolved == sha
    assert (proj / ".claude/commands/tools/review.md").read_text("utf-8") == "v1\n"


def test_fresh_checkout_reproduces_from_config_and_lock(tmp_path: Path, monkeypatch):
    proj, _, _, _, mk_json = _install_fixture(
        tmp_path, monkeypatch, {"commands/review.md": "# r\n"}
    )
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / ".agentry.yml").write_bytes((proj / ".agentry.yml").read_bytes())
    lock_path(fresh).write_bytes(lock_path(proj).read_bytes())
    mk_json.unlink()  # no marketplace access at all

    assert _cli(fresh, monkeypatch, "sync").exit_code == 0
    assert (fresh / ".claude/commands/tools/review.md").read_text("utf-8") == "# r\n"


# --- the plugin root (link_from: source_root) -----------------------------------------------


_ROOT_HOOK = {
    "hooks/run.sh": "echo hi\n",
    ".claude-plugin/plugin.json": json.dumps(
        {
            "name": "tools",
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh"}
                        ]
                    }
                ]
            },
        }
    ),
}


def test_inline_plugin_root_hook_installs_via_source_root_link_on_claude(
    tmp_path: Path, monkeypatch
):
    proj, *_ = _install_fixture(tmp_path, monkeypatch, _ROOT_HOOK)
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0

    link = proj / ".claude/plugins/agentry/tools/plugin-hooks"
    assert link.is_symlink()
    assert (link / "hooks" / "run.sh").read_text("utf-8") == "echo hi\n"  # the plugin dir itself
    assert (link / ".claude-plugin" / "plugin.json").is_file()
    settings = json.loads((proj / ".claude/settings.json").read_text("utf-8"))
    (cmd,) = [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]]
    assert cmd == "${CLAUDE_PROJECT_DIR}/.claude/plugins/agentry/tools/plugin-hooks/hooks/run.sh"


def test_plugin_root_hook_still_skipped_on_non_claude_target(tmp_path: Path, monkeypatch):
    proj, *_ = _install_fixture(tmp_path, monkeypatch, _ROOT_HOOK, targets=("claude", "gemini"))
    res = _cli(proj, monkeypatch, "add", "tools")
    assert res.exit_code == 0
    gem = proj / ".gemini/settings.json"
    assert not gem.exists() or "Stop" not in json.loads(gem.read_text("utf-8")).get("hooks", {})
    assert "skipped it on gemini" in _text(res)


def test_link_from_default_artifact_unchanged(tmp_path: Path):
    """Guard (passes today): a link+merge profile with no `link_from` links the artifact's
    parent dir, exactly as before MP1."""
    from agentry.models import Component, ComponentType, Source, SourceType
    from agentry.reconcile import sync

    src = tmp_path / "src"
    _write(src, "hooks/run.sh", "echo\n")
    _write(
        src,
        "hooks/hooks.json",
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "r"}]}]}}),
    )
    proj = _project(tmp_path)
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.HOOK, name="hooks", path="hooks"))
    store.doc["target_profiles"] = {
        "claude": {
            "hook": {
                "strategy": "link+merge",
                "dest": ".claude/hooks/agentry/{name}",
                "file": ".claude/settings.json",
                "pointer": "hooks",
            }
        }
    }
    store.save()
    sync(proj)
    link = proj / ".claude/hooks/agentry/hooks"
    assert link.is_symlink() and (link / "run.sh").is_file() and (link / "hooks.json").is_file()


# --- collisions ---------------------------------------------------------------------------


def test_two_plugins_same_skill_first_wins_grouped_line(tmp_path: Path, monkeypatch):
    """Skills stay flat on claude, so two plugins shipping the same skill collide (T22): the
    later plugin gets ONE grouped line for 2+ units, an individual line for 1, and doctor keeps
    one line per unit."""

    def skills(*names: str, who: str) -> dict[str, str]:
        return {f"skills/{n}/SKILL.md": f"---\nname: {n}\ndescription: {who}\n---\n" for n in names}

    _, url_a, sha_a = _plugin_repo(tmp_path, "aaa", skills("review", "lint", who="aaa"))
    _, url_b, sha_b = _plugin_repo(tmp_path, "bbb", skills("review", "lint", who="bbb"))
    _, url_c, sha_c = _plugin_repo(tmp_path, "ccc", skills("review", who="ccc"))
    path = _marketplace(
        tmp_path / "mk",
        [
            _url_entry("aaa", url_a, sha_a),
            _url_entry("bbb", url_b, sha_b),
            _url_entry("ccc", url_c, sha_c),
        ],
    )
    proj = _project(tmp_path)
    _cli(proj, monkeypatch, "catalog", "add", "mk", str(path))
    for name in ("aaa", "bbb", "ccc"):
        _cli(proj, monkeypatch, "add", name)

    out = _text(_cli(proj, monkeypatch, "sync"))
    assert "description: aaa" in (proj / ".claude/skills/review/SKILL.md").read_text("utf-8")
    grouped = (
        "bbb: 2 entries are already installed by aaa, listed earlier in .agentry.yml — skipped ("
    )
    assert out.count(grouped) == 1  # bbb: ONE grouped line for its 2 units
    tail = out.split(grouped, 1)[1]
    units, rest = tail.split(")", 1)
    assert sorted(u.strip() for u in units.split(",")) == sorted(
        [".claude/skills/lint", ".claude/skills/review"]
    )
    assert rest.startswith(". Disable one of them, or reorder components to choose which wins.")
    assert ".claude/skills/review is already installed by" in out  # ccc: individual

    from agentry.doctor import run_checks

    w5 = [c.message for c in run_checks(proj) if "already installed by" in c.message]
    assert len(w5) == 3  # one per unit


def test_plugin_commands_namespaced_on_claude_still_collide_elsewhere(tmp_path: Path, monkeypatch):
    """PM Q1 final: plugin commands install under .claude/commands/<plugin>/ (giving
    /<plugin>:name), so two plugins' review.md coexist on claude; on opencode they stay flat
    and still collide."""
    _, url_a, sha_a = _plugin_repo(tmp_path, "aaa", {"commands/review.md": "# from aaa\n"})
    _, url_b, sha_b = _plugin_repo(tmp_path, "bbb", {"commands/review.md": "# from bbb\n"})
    path = _marketplace(
        tmp_path / "mk", [_url_entry("aaa", url_a, sha_a), _url_entry("bbb", url_b, sha_b)]
    )
    proj = _project(tmp_path, ("claude", "opencode"))
    _cli(proj, monkeypatch, "catalog", "add", "mk", str(path))
    _cli(proj, monkeypatch, "add", "aaa")
    _cli(proj, monkeypatch, "add", "bbb")

    out = _text(_cli(proj, monkeypatch, "sync"))
    assert (proj / ".claude/commands/aaa/review.md").read_text("utf-8") == "# from aaa\n"
    assert (proj / ".claude/commands/bbb/review.md").read_text("utf-8") == "# from bbb\n"
    assert not (proj / ".claude/commands/review.md").exists()  # never flat on claude
    assert (proj / ".opencode/commands/review.md").read_text("utf-8") == "# from aaa\n"
    assert ".opencode/commands/review.md is already installed by" in out
    assert ".claude/commands/" not in out.split("already installed by")[0]  # no claude W5


def test_unsafe_plugin_name_falls_back_to_source_name(tmp_path: Path, monkeypatch):
    """A plugin name that isn't a safe path segment never becomes a directory: the command
    namespace falls back to the source name, with a warning naming the plugin."""
    _, url, sha = _plugin_repo(tmp_path, "weird", {"commands/review.md": "# r\n"})
    path = _marketplace(tmp_path / "mk", [_url_entry("../evil", url, sha)])
    proj = _project(tmp_path)
    _cli(proj, monkeypatch, "catalog", "add", "mk", str(path))
    res = _cli(proj, monkeypatch, "add", "mk/../evil")
    out = _text(res)
    (source,) = [s for s in ConfigStore.load(proj).parsed().sources if s.url == url]
    assert source.name and "/" not in source.name and ".." not in source.name
    assert (proj / f".claude/commands/{source.name}/review.md").read_text("utf-8") == "# r\n"
    assert not (tmp_path / "evil").exists() and not (proj / ".claude/evil").exists()
    assert source.name == "evil"
    assert (
        "evil: plugin name '../evil' isn't a safe folder name — its commands install "
        ".claude/commands/evil/ instead. Use `--name` to choose the folder."
    ) in out


# --- warnings -----------------------------------------------------------------------------


def test_unsupported_keys_one_line_per_plugin(tmp_path: Path, monkeypatch):
    manifest = {"name": "tools", "outputStyles": {"x": {}}, "lspServers": {"y": {}}}
    proj, *_ = _install_fixture(
        tmp_path,
        monkeypatch,
        {"commands/c.md": "# c\n", ".claude-plugin/plugin.json": json.dumps(manifest)},
    )
    out = _text(_cli(proj, monkeypatch, "add", "tools"))
    assert out.count("no agentry equivalent") == 1
    assert "`outputStyles` and `lspServers` have no agentry equivalent" in out


@pytest.mark.parametrize(
    "lockfile, cmd",
    [
        ("package-lock.json", "npm ci"),
        ("bun.lock", "bun install --frozen-lockfile"),
        ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
        ("yarn.lock", "yarn install --frozen-lockfile"),
    ],
)
def test_npm_lockfile_warns_runs_nothing(tmp_path: Path, monkeypatch, lockfile: str, cmd: str):
    proj, *_ = _install_fixture(
        tmp_path,
        monkeypatch,
        {"commands/c.md": "# c\n", "package.json": "{}\n", lockfile: "{}\n"},
    )
    out = _text(_cli(proj, monkeypatch, "add", "tools"))
    assert (
        f"tools: this plugin ships {lockfile}; its hooks or MCP servers may need node_modules. "
        f"agentry doesn't run installers — run `{cmd}` in .agentry/tools yourself."
    ) in out
    assert not list(proj.rglob("node_modules"))  # nothing was run


def test_plugin_data_warning_applies(tmp_path: Path, monkeypatch):
    manifest = {
        "name": "tools",
        "mcpServers": {"db": {"command": "db", "args": ["${CLAUDE_PLUGIN_DATA}/x"]}},
    }
    proj, *_ = _install_fixture(
        tmp_path, monkeypatch, {".claude-plugin/plugin.json": json.dumps(manifest)}
    )
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    out = _text(_cli(proj, monkeypatch, "add", "tools"))
    assert (
        "tools/mcp/plugin-mcp: references ${CLAUDE_PLUGIN_DATA}, which only exists inside an "
        "installed Claude plugin — installed as-is, but it won't resolve."
    ) in out


# --- repeat runs --------------------------------------------------------------------------


def test_marketplace_install_second_sync_idempotent(tmp_path: Path, monkeypatch):
    proj, *_ = _install_fixture(
        tmp_path, monkeypatch, {"commands/review.md": "# r\n", **_ROOT_HOOK}
    )
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    from agentry.reconcile import sync

    before = _snapshot(proj)
    res = sync(proj)
    assert res.created == [] and res.updated == [] and res.removed == []
    assert _snapshot(proj) == before


# --- PM sign-off conditions M1, M2 ---------------------------------------------------------


def test_update_sha_pinned_plugin_prints_not_moved(tmp_path: Path, monkeypatch):
    """M1: a sha pin is never a silent no-op."""
    proj, _, _, sha, _ = _install_fixture(tmp_path, monkeypatch, {"commands/review.md": "v1\n"})
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    assert "pinned_by" in (proj / ".agentry.yml").read_text("utf-8")
    out = _text(_cli(proj, monkeypatch, "update", "tools"))
    assert (
        f"tools: pinned to {sha[:7]} by marketplace 'mk' — not moved. Re-run `agentry add tools` "
        "to take the marketplace's current pin."
    ) in out


def test_readd_plugin_takes_new_marketplace_sha_prints_old_new(tmp_path: Path, monkeypatch):
    """M1: re-adding after the marketplace's sha moved rewrites the ref and says so."""
    proj, repo, url, old_sha, mk_json = _install_fixture(
        tmp_path, monkeypatch, {"commands/review.md": "v1\n"}
    )
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    _write(repo, "commands/review.md", "v2\n")
    new_sha = _commit_all(repo, "v2")
    _marketplace(mk_json.parent.parent, [_url_entry("tools", url, new_sha)])

    out = _text(_cli(proj, monkeypatch, "add", "tools"))
    assert f"tools: {old_sha[:7]} → {new_sha[:7]}" in out
    (source,) = [s for s in ConfigStore.load(proj).parsed().sources if s.name == "tools"]
    assert source.ref == new_sha
    assert (proj / ".claude/commands/tools/review.md").read_text("utf-8") == "v2\n"


def test_add_plugin_with_no_installable_components_exits_1_writes_nothing(
    tmp_path: Path, monkeypatch
):
    """M2: no empty success."""
    proj, _, _, _, mk_json = _install_fixture(
        tmp_path,
        monkeypatch,
        {".claude-plugin/plugin.json": json.dumps({"name": "tools", "outputStyles": {"x": {}}})},
    )
    assert "tools" in _catalog(proj, mk_json).repositories  # it IS listed: the refusal is M2's
    cfg_before = (proj / ".agentry.yml").read_bytes()
    res = _cli(proj, monkeypatch, "add", "tools")
    assert res.exit_code == 1
    assert "No catalog lists" not in _text(res)
    assert "tools: it ships no components agentry can install — nothing was added." in _text(res)
    assert (proj / ".agentry.yml").read_bytes() == cfg_before  # no source, no component
    assert not lock_path(proj).exists() or load_lock(proj).entry("tools") is None


# --- PM "MP1, rulings": A (per-source profiles), why visibility, Q2 ----------------------


def test_user_hooks_source_stays_plain_merge_after_marketplace_add(tmp_path: Path, monkeypatch):
    """A: the plugin's link+merge profile is scoped to the plugin source (`profiles:`), so a
    user's own hooks/*.json source keeps installing as a plain merge — no link, no rewrite."""
    from agentry.manifest import load_manifest
    from agentry.models import Component, ComponentType, Source, SourceType

    proj, *_ = _install_fixture(tmp_path, monkeypatch, _ROOT_HOOK)
    mine = tmp_path / "mine"
    user_group = {"hooks": [{"type": "command", "command": "user-hook"}]}
    _write(mine, "hooks/guard.json", json.dumps({"hooks": {"SessionStart": [user_group]}}))
    store = ConfigStore.load(proj)
    store.add_source(Source(name="mine", type=SourceType.LOCAL, path=str(mine)))
    store.add_component(Component(source="mine", type=ComponentType.HOOK, name="guard"))
    store.save()

    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    settings = json.loads((proj / ".claude/settings.json").read_text("utf-8"))["hooks"]
    assert settings["SessionStart"] == [user_group]  # the user's hook, untouched and unrewritten
    manifest = load_manifest(proj)
    assert any(m.component == "mine/hook/guard" for m in manifest.merges)  # plain merge
    assert not any(lm.component == "mine/hook/guard" for lm in manifest.link_merges)
    assert not (proj / ".claude/plugins/agentry/mine").exists()
    assert (proj / ".claude/plugins/agentry/tools/plugin-hooks").is_symlink()  # the plugin's


def test_why_shows_source_profile_decided_placement(tmp_path: Path, monkeypatch):
    """PM condition on A: `agentry why` shows when a source's own `profiles:` decided where a
    component installs."""
    proj, *_ = _install_fixture(tmp_path, monkeypatch, _ROOT_HOOK)
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    why = _text(_cli(proj, monkeypatch, "why", "tools/hook/plugin-hooks"))
    assert "placement: source profile (claude.hook)" in why
    assert ".claude/plugins/agentry/tools/plugin-hooks" in why


def test_hook_only_plugin_on_codex_only_project_exits_1(tmp_path: Path, monkeypatch):
    """Q2/M2: nothing installable for the project's targets (a hook-only plugin, codex only)
    -> `add` exits 1, names the shipped types and the targets, and writes nothing."""
    plain_hook = json.dumps(
        {"name": "tools", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "c"}]}]}}
    )
    proj, *_ = _install_fixture(
        tmp_path, monkeypatch, {".claude-plugin/plugin.json": plain_hook}, targets=("codex",)
    )
    cfg_before = (proj / ".agentry.yml").read_bytes()
    res = _cli(proj, monkeypatch, "add", "tools")
    assert res.exit_code == 1
    out = _text(res)
    assert "No catalog lists" not in out
    assert (
        "tools: none of its components install into your targets (codex) — it ships hook, which "
        "codex doesn't support. Nothing was added."
    ) in out
    assert (proj / ".agentry.yml").read_bytes() == cfg_before


def test_default_branch_read_only_by_add_for_unpinned_entry(tmp_path: Path, monkeypatch):
    """C: `git ls-remote` (registry.default_branch) runs only in `agentry add`, only for an
    entry with neither ref nor sha — never in catalog add, sync or status."""
    _, url, _ = _plugin_repo(tmp_path, "tools", {"commands/review.md": "# r\n"})
    path = _marketplace(tmp_path / "mk", [_url_entry("tools", url)])  # no sha, no ref
    proj = _project(tmp_path)
    calls: list[str] = []
    real = reg.default_branch
    monkeypatch.setattr(reg, "default_branch", lambda u: calls.append(u) or real(u))

    assert _cli(proj, monkeypatch, "catalog", "add", "mk", str(path)).exit_code == 0
    assert calls == []
    assert _cli(proj, monkeypatch, "add", "tools").exit_code == 0
    assert len(calls) == 1
    for cmd in ("sync", "status"):
        assert _cli(proj, monkeypatch, cmd).exit_code == 0
    assert len(calls) == 1
    (source,) = [s for s in ConfigStore.load(proj).parsed().sources if s.name == "tools"]
    assert source.ref == "main"


def test_unreadable_default_branch_falls_back_to_main_with_warning(tmp_path: Path, monkeypatch):
    _, url, _ = _plugin_repo(tmp_path, "tools", {"commands/review.md": "# r\n"})
    path = _marketplace(tmp_path / "mk", [_url_entry("tools", url)])
    proj = _project(tmp_path)
    monkeypatch.setattr(reg, "default_branch", lambda u: None)
    assert _cli(proj, monkeypatch, "catalog", "add", "mk", str(path)).exit_code == 0
    out = _text(_cli(proj, monkeypatch, "add", "tools"))
    assert (
        f"! tools: couldn't read the default branch of {url} — used 'main'. Add the marketplace "
        "by a tree URL (…/tree/<branch>) to choose the branch."
    ) in out
    (source,) = [s for s in ConfigStore.load(proj).parsed().sources if s.name == "tools"]
    assert source.ref == "main"


def test_hook_only_plugin_two_targets_q2_plural(tmp_path: Path, monkeypatch):
    plain_hook = json.dumps(
        {"name": "tools", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "c"}]}]}}
    )
    proj, *_ = _install_fixture(
        tmp_path,
        monkeypatch,
        {".claude-plugin/plugin.json": plain_hook},
        targets=("codex", "cursor"),
    )
    res = _cli(proj, monkeypatch, "add", "tools")
    assert res.exit_code == 1
    assert (
        "tools: none of its components install into your targets (codex, cursor) — it ships "
        "hook, which codex, cursor don't support. Nothing was added."
    ) in _text(res)


def test_source_profile_on_undefined_target_warns_and_is_ignored(tmp_path: Path, monkeypatch):
    """A source's `profiles:` naming a target the project can't resolve is ignored with a
    warning (sync and doctor); a defined-but-inactive target doesn't warn."""
    from agentry.doctor import run_checks
    from agentry.models import Component, ComponentType, Source, SourceType

    src = tmp_path / "s"
    _write(src, "commands/c.md", "# c\n")
    proj = _project(tmp_path)
    store = ConfigStore.load(proj)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.COMMAND, name="c"))
    rule = {"strategy": "link", "dest": ".x/{name}.md"}
    for entry in store.doc["sources"]:
        if entry["name"] == "s":
            entry["profiles"] = {"nosuch": {"command": rule}, "opencode": {"command": rule}}
    store.save()

    out = _text(_cli(proj, monkeypatch, "sync"))
    head = (
        "source 's': profiles: sets target 'nosuch', which this project doesn't define — ignored."
    )
    tail = "target add nosuch`), or remove it from the source's profiles."
    assert out.count(head) == 1 and tail in out
    assert "target 'opencode'" not in out  # built-in, merely inactive: no warning
    assert (proj / ".claude/commands/c.md").is_symlink()  # the claude default still applies
    assert not (proj / ".x").exists()
    checks = run_checks(proj)
    assert [c.level for c in checks if c.category == "target" and c.message.startswith(head)] == [
        "warn"
    ]
    assert not [c for c in checks if c.category == "plugin" and c.message.startswith(head)]


# --- ADR-003 name units for plugin agents ---------------------------------------------------


def _agents(*names: str, who: str) -> dict[str, str]:
    """Each agent's file stem differs per plugin; only the frontmatter `name` is shared."""
    return {f"agents/{who}-{n}.md": f"---\nname: {n}\ndescription: {who}\n---\n" for n in names}


def test_two_plugins_same_agent_names_w5_name_grouped(tmp_path: Path, monkeypatch):
    """Two plugins ship agents named `reviewer` and `linter` (at different paths): the later
    plugin loses both name units — ONE grouped line on sync, the W5-name lines under -v."""
    _, url_a, sha_a = _plugin_repo(tmp_path, "aaa", _agents("reviewer", "linter", who="aaa"))
    _, url_b, sha_b = _plugin_repo(tmp_path, "bbb", _agents("reviewer", "linter", who="bbb"))
    path = _marketplace(
        tmp_path / "mk", [_url_entry("aaa", url_a, sha_a), _url_entry("bbb", url_b, sha_b)]
    )
    proj = _project(tmp_path)
    _cli(proj, monkeypatch, "catalog", "add", "mk", str(path))
    _cli(proj, monkeypatch, "add", "aaa")
    _cli(proj, monkeypatch, "add", "bbb")

    out = _text(_cli(proj, monkeypatch, "sync"))
    agents = proj / ".claude" / "agents"
    installed = sorted(p.relative_to(agents).as_posix() for p in agents.rglob("*.md"))
    assert [i for i in installed if "bbb-" in i] == []  # every bbb agent skipped
    assert sorted(i.rsplit("/", 1)[-1] for i in installed) == ["aaa-linter.md", "aaa-reviewer.md"]
    grouped = (
        "bbb: 2 entries are already installed by aaa, listed earlier in .agentry.yml — skipped ("
    )
    assert out.count(grouped) == 1
    units, rest = out.split(grouped, 1)[1].split(")", 1)
    assert sorted(u.strip() for u in units.split(",")) == [
        "agent name 'linter' on claude",
        "agent name 'reviewer' on claude",
    ]
    assert rest.startswith(". Disable one of them, or reorder components to choose which wins.")
    assert "loads only one agent per name" not in out  # grouped, not individual, without -v

    from agentry.doctor import run_checks

    collisions = [c.message for c in run_checks(proj) if c.category == "collision"]
    assert len([m for m in collisions if m.startswith("bbb/agent/")]) == 2  # one per unit

    verbose = _text(_cli(proj, monkeypatch, "sync", "-v"))
    for name in ("reviewer", "linter"):
        line = (
            f"bbb/agent/bbb-{name}: agent name '{name}' on claude is already used by "
            f"aaa/agent/aaa-{name}, listed earlier in .agentry.yml — skipped (claude loads only "
            "one agent per name). Disable one of them, or reorder components to choose which wins."
        )
        assert verbose.count(line) == 1, name


def test_plugin_agent_matching_user_agent_refused_w1_name(tmp_path: Path, monkeypatch):
    proj, *_ = _install_fixture(tmp_path, monkeypatch, _agents("reviewer", who="tools"))
    mine = proj / ".claude" / "agents" / "mine.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: reviewer\ndescription: mine\n---\nmine\n", encoding="utf-8")
    before = mine.read_bytes()

    snap = _snapshot(proj)
    res = _cli(proj, monkeypatch, "add", "tools")
    assert res.exit_code == 0, res.output  # PM (b): added, not an M2 refusal
    out = _text(res)
    w1 = (
        "tools/agent/tools-reviewer: agent name 'reviewer' on claude is already used by your "
        "agent .claude/agents/mine.md — skipped (claude loads only one agent per name). Rename "
        "one of them, or remove yours to install tools/agent/tools-reviewer's."
    )
    assert w1 in out
    assert (
        "Added tools, but nothing from it is installed yet — 1 component(s) blocked by existing "
        "entries (see warnings above, or `agentry status`)."
    ) in out
    refs = [c.ref for c in ConfigStore.load(proj).parsed().components if c.source == "tools"]
    assert refs == ["tools/agent/tools-reviewer"]  # config added
    assert _snapshot(proj) == snap  # no target writes; the user's agent byte-identical
    assert mine.read_bytes() == before

    from agentry.reconcile import status

    (row,) = [r for r in status(proj)[0] if r.ref == "tools/agent/tools-reviewer"]
    assert row.state == "missing"
    assert row.note == "agent name 'reviewer' is used by your agent .claude/agents/mine.md"

    assert w1 in _text(_cli(proj, monkeypatch, "sync"))
    assert _snapshot(proj) == snap
    assert _cli(proj, monkeypatch, "doctor", "--strict").exit_code == 1
    mine.unlink()  # control: with the blocker gone, the plugin installs and --strict passes
    assert _cli(proj, monkeypatch, "sync").exit_code == 0
    assert list((proj / ".claude" / "agents").rglob("tools-reviewer.md"))
    assert _cli(proj, monkeypatch, "doctor", "--strict").exit_code == 0


def test_source_profile_identity_ignored_with_warning(tmp_path: Path, monkeypatch):
    """`identity` describes the target tool, so it can only be set in target_profiles: a
    source's `profiles:` that sets it is ignored with a warning, and that source's agent
    still collides by name."""
    from agentry.models import Component, ComponentType, Source, SourceType

    def local(label: str, stem: str) -> Path:
        root = tmp_path / label
        _write(root, f"agents/{stem}.md", "---\nname: reviewer\ndescription: d\n---\n")
        return root

    proj = _project(tmp_path)
    store = ConfigStore.load(proj)
    for label, stem in (("one", "a"), ("free", "f")):
        store.add_source(Source(name=label, type=SourceType.LOCAL, path=str(local(label, stem))))
        store.add_component(Component(source=label, type=ComponentType.AGENT, name=stem))
    rule = {"strategy": "link", "dest": ".claude/agents/{name}.md", "identity": None}
    for entry in store.doc["sources"]:
        if entry["name"] == "free":
            entry["profiles"] = {"claude": {"agent": rule}}
    store.save()

    out = _text(_cli(proj, monkeypatch, "sync", "-v"))
    assert (
        out.count(
            "source 'free': profiles: sets `identity` for claude.agent — ignored; identity describes "
            "how the target tool names components and can only be set in target_profiles."
        )
        == 1
    )
    agents = proj / ".claude" / "agents"
    assert (agents / "a.md").is_symlink() and not (agents / "f.md").exists()
    assert "free/agent/f: agent name 'reviewer' on claude is already used by one/agent/a" in out

    from agentry.doctor import run_checks

    warn = (
        "source 'free': profiles: sets `identity` for claude.agent — ignored; identity describes "
        "how the target tool names components and can only be set in target_profiles."
    )
    assert [(c.level, c.category) for c in run_checks(proj) if c.message == warn] == [
        ("warn", "target")
    ]


def test_source_profile_identity_set_on_target_without_rule_has_no_effect(
    tmp_path: Path, monkeypatch
):
    """A source can't switch the name rule ON either: opencode has no identity rule, so two
    same-name agents both install there despite the source's `identity: frontmatter:name`."""
    from agentry.models import Component, ComponentType, Source, SourceType

    proj = _project(tmp_path, ("opencode",))
    store = ConfigStore.load(proj)
    for label, stem in (("one", "a"), ("two", "b")):
        root = tmp_path / label
        _write(root, f"agents/{stem}.md", "---\nname: reviewer\ndescription: d\n---\n")
        store.add_source(Source(name=label, type=SourceType.LOCAL, path=str(root)))
        store.add_component(Component(source=label, type=ComponentType.AGENT, name=stem))
    rule = {
        "strategy": "link",
        "dest": ".opencode/agents/{name}.md",
        "identity": "frontmatter:name",
    }
    for entry in store.doc["sources"]:
        if entry["name"] == "two":
            entry["profiles"] = {"opencode": {"agent": rule}}
    store.save()

    out = _text(_cli(proj, monkeypatch, "sync", "-v"))
    assert (
        out.count(
            "source 'two': profiles: sets `identity` for opencode.agent — ignored; identity describes "
            "how the target tool names components and can only be set in target_profiles."
        )
        == 1
    )
    assert (proj / ".opencode/agents/a.md").exists() and (proj / ".opencode/agents/b.md").exists()
    assert "agent name 'reviewer'" not in out
