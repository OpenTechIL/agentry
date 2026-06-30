"""MCP correctness as *tested* guarantees (apm pain-points ideas 5b/5c).

These lock down behavior agentry already has, so it can't regress:

* **No-overwrite** — merging a managed MCP server into a hand-authored config file
  (``.vscode/mcp.json`` for Copilot, ``.mcp.json`` for Claude) preserves every entry the
  user wrote, and removing the managed server leaves the hand-authored ones byte-intact.
  Counters apm #20 (``context7`` overwrote a user's ``.vscode/mcp.json``).
* **Scoped-name non-collision** — two servers whose keys share a suffix (``@acme/mcp`` vs
  ``@other/mcp``) install as two distinct keys; agentry writes the fragment's top-level key
  verbatim and never truncates a scoped name to ``mcp``. Counters apm #1693 (last-write-wins
  silently dropped a server).
"""

from __future__ import annotations

import json
from pathlib import Path

from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType
from agentry.reconcile import status, sync


def _mcp_source(root: Path, **servers: dict) -> Path:
    """A source dir providing one ``mcp/<name>.json`` per kwarg (name → server entry)."""
    src = root
    (src / "mcp").mkdir(parents=True)
    for name, entry in servers.items():
        # The component name is the file stem; the *key* is the fragment's top-level key,
        # which is what lands in the merged config. Use the entry's own declared key.
        key = entry.pop("__key__", name)
        (src / "mcp" / f"{name}.json").write_text(json.dumps({key: entry}))
    return src


def _target_only(project: Path, target: str) -> ConfigStore:
    store = ConfigStore.load(project)
    store.doc["targets"] = [target]
    return store


# --- Guarantee 1: never overwrite a hand-authored MCP config -----------------------------


def test_copilot_merge_preserves_hand_authored_vscode_mcp_json(project: Path, local_source: Path):
    """Installing into Copilot's ``.vscode/mcp.json`` keeps the user's own servers + siblings."""
    store = _target_only(project, "copilot")
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(local_source)))
    store.add_component(Component(source="s", type=ComponentType.MCP, name="github", enabled=True))
    store.save()

    # The user already has their own .vscode/mcp.json with a server *and* a sibling key.
    vscode = project / ".vscode"
    vscode.mkdir()
    hand = {
        "servers": {"my-server": {"command": "mine", "args": ["--keep"]}},
        "inputs": [{"id": "token", "type": "promptString"}],
    }
    (vscode / "mcp.json").write_text(json.dumps(hand))

    res = sync(project)
    assert not res.warnings, res.warnings

    after = json.loads((vscode / "mcp.json").read_text())
    assert after["servers"]["github"]  # managed server merged in
    assert after["servers"]["my-server"] == {"command": "mine", "args": ["--keep"]}  # untouched
    assert after["inputs"] == hand["inputs"]  # non-server sibling untouched

    # Removing the managed server leaves the hand-authored entries byte-intact.
    store = ConfigStore.load(project)
    store.set_enabled("s/mcp/github", False)
    store.save()
    sync(project)

    final = json.loads((vscode / "mcp.json").read_text())
    assert "github" not in final["servers"]
    assert final["servers"]["my-server"] == {"command": "mine", "args": ["--keep"]}
    assert final["inputs"] == hand["inputs"]


# --- Guarantee 2: scoped names never collide ---------------------------------------------


def test_scoped_mcp_names_do_not_collide(project: Path, tmp_path: Path):
    """``@acme/mcp`` and ``@other/mcp`` install as two distinct keys — no truncation to ``mcp``."""
    src = _mcp_source(
        tmp_path / "scoped",
        acme={"__key__": "@acme/mcp", "command": "acme"},
        other={"__key__": "@other/mcp", "command": "other"},
    )
    store = ConfigStore.load(project)  # claude target → .mcp.json / mcpServers
    store.add_source(Source(name="s", type=SourceType.LOCAL, path=str(src)))
    store.add_component(Component(source="s", type=ComponentType.MCP, name="acme", enabled=True))
    store.add_component(Component(source="s", type=ComponentType.MCP, name="other", enabled=True))
    store.save()

    res = sync(project)
    assert not res.warnings, res.warnings

    servers = json.loads((project / ".mcp.json").read_text())["mcpServers"]
    assert servers["@acme/mcp"] == {"command": "acme"}
    assert servers["@other/mcp"] == {"command": "other"}
    assert "mcp" not in servers  # the scoped names were NOT truncated to a shared "mcp" key
    assert len(servers) == 2  # both survive — no last-write-wins drop

    rows, _ = status(project)
    assert all(r.state == "ok" for r in rows)

    # Reversible per-server: disabling one drops exactly its key, leaving the other.
    store = ConfigStore.load(project)
    store.set_enabled("s/mcp/acme", False)
    store.save()
    sync(project)
    after = json.loads((project / ".mcp.json").read_text())["mcpServers"]
    assert "@acme/mcp" not in after
    assert after["@other/mcp"] == {"command": "other"}
