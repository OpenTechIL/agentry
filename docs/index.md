# agentry

**A dependency manager for AI coding agents.** `agentry` (command: `agy`) lets you
declare the skills, agents, commands, tools, hooks, and MCP servers your project uses —
then install them into Claude Code, OpenCode, and Cursor with one command.

## The idea

Treat AI components like packages:

- **`.agentry.yml`** — a single, version-controlled file declaring your sources and components.
- **`.agentry.lock`** — exact resolved commit SHAs for deterministic, reproducible installs.
- **`.agentry/`** — a local store (git clones / local copies), git-ignored like `node_modules`.
- One `agy sync` installs everything into each tool's native layout — via **symlinks**
  (skills/agents/commands/tools) or **reversible config merges** (hooks/MCP).

## Get started

Run straight from git with [`uv`](https://docs.astral.sh/uv/) — no global install needed:

```bash
uvx --from git+https://github.com/opentech/agentry agy --help
```

See the [README](https://github.com/opentech/agentry#readme) for the full quickstart and
command reference.

## Learn more

- **[Architecture](architecture.md)** — the design, data model, reconcile flow, and safety
  invariants (the source of truth for behavior).
- **[Knowledge base](knowledge-base.md)** — patterns, pitfalls, and discoveries.
- **[Branding](branding-kit.md)** — logo and brand guidelines.
- **[Contributing](https://github.com/opentech/agentry/blob/main/CONTRIBUTING.md)** — dev
  setup, conventions, and how to add targets/component types.
