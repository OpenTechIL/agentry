# agentry

**A dependency manager for AI coding agents.** `agentry` (command: `agy`) lets you
declare the skills, agents, commands, tools, hooks, and MCP servers your project uses —
then install them into Claude Code, Cursor, Gemini CLI, OpenCode, Codex, Windsurf, Kimi,
GitHub Copilot, and Kiro with one command. **Write once, deploy to any agent** — and teach
it new agents without writing code.

## The idea

Declare your components once; `agy sync` installs them into every agent you target — each in
its own native layout:

```mermaid
%%{init: {'theme':'base','themeVariables':{'primaryColor':'#5A4FCF','primaryTextColor':'#F8FAFC','lineColor':'#22D3EE','primaryBorderColor':'#22D3EE','secondaryColor':'#1E1E2E'}}}%%
flowchart LR
  Y["<b>.agentry.yml</b><br/>declare once"] --> S{{"agy sync"}}
  S --> C["Claude Code<br/><code>.claude/</code>"]
  S --> U["Cursor<br/><code>.cursor/</code>"]
  S --> G["Gemini CLI<br/><code>.gemini/</code>"]
  S --> O["OpenCode<br/><code>.opencode/</code>"]
  S --> X["Codex · Windsurf · Kimi · Copilot · Kiro"]
  S -.->|"target_profiles — no code"| N["your own agent"]
```

Treat AI components like packages:

- **`.agentry.yml`** — a single, version-controlled file declaring your sources and components.
- **`.agentry.lock`** — exact resolved commit SHAs for deterministic, reproducible installs.
- **`.agentry/`** — a local store (git clones / local copies), git-ignored like `node_modules`.
- One `agy sync` installs everything into each tool's native layout — via **symlinks**
  (skills/agents/commands/tools) or **reversible config merges** (hooks/MCP).

What makes it different — it optimizes *editing* agent context and refuses to quietly break a
project (no compile step, no static artifact to regenerate, no silent overwrites):

- **Edit once — every agent sees it instantly.** Live symlinks into one store; no recompile.
- **Any agent — even your own.** Open-string targets + shareable driver overlays
  (`agy target add`); a new or in-house agent is a few lines of `target_profiles`, no fork.
- **It never touches what you wrote.** Key-scoped merges, never-clobber symlinks, clean
  `agy remove` — CI-enforced guarantees.
- **Loud, never silent.** `agy doctor` catches undefined targets, unset `${VARs}`, and drift
  before they bite; status/`agy why` share the install resolver (no phantom drift).
- **Reproducible by default.** Timestamp-free lockfile + `agy sync --frozen` for clean CI.
- **A dependency manager, not a runtime** — nothing of it runs while your agents do; no model
  or key embedded.
- **Portable & interoperable** — emit `AGENTS.md`, import/consume other agent-package formats,
  and translate component content when a tool needs a different shape.

## Get started

Run straight from git with [`uv`](https://docs.astral.sh/uv/) — no global install needed:

```bash
uvx --from git+https://github.com/OpenTechIL/agentry agy --help
```

See the [README](https://github.com/OpenTechIL/agentry#readme) for the full quickstart and
command reference.

## Learn more

- **[Architecture](architecture.md)** — the design, data model, reconcile flow, and safety
  invariants (the source of truth for behavior).
- **[Knowledge base](knowledge-base.md)** — patterns, pitfalls, and discoveries.
- **[Branding](branding-kit.md)** — logo and brand guidelines.
- **[Contributing](https://github.com/OpenTechIL/agentry/blob/main/CONTRIBUTING.md)** — dev
  setup, conventions, and how to add targets/component types.
