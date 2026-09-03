# Authoring

How to write a component repo other projects can install, and how to get it listed in a
catalog so `agentry add <name>` resolves it by name.

Authoring is deliberately low-ceremony: **a conventional layout needs no agentry-specific
file at all.** Reach for a descriptor or a catalog entry only when you outgrow that.

## Component repos

### The conventional layout (no config needed)

Mirror the standard agent layout and discovery finds everything:

```
skills/<name>/          directory (contains SKILL.md)     → link
agents/<name>.md        file                              → link
commands/<name>.md      file                              → link
tools/<name>/           directory                         → link
hooks/<name>.json       JSON object of named entries      → merge
mcp/<name>.json         JSON object of named entries      → merge
```

The component *type* dictates the shape — directory vs file, and the extension. A consumer
then writes nothing but the source:

```bash
agentry source add my-kit https://github.com/you/my-kit
agentry list                          # the real names your repo provides
agentry add my-kit/skill/<name>
```

Two conveniences worth knowing about:

- **Monorepos.** If components live under a subdirectory, consumers pass
  `--subdir packages/agent-kit`; you don't need to restructure.
- **Per-harness merge variants.** For `hooks`/`mcp` only, a `<base>-<harness>.json` filename
  (e.g. `hooks-cursor.json`) routes that fragment to that harness instead of the canonical
  one. Recognized suffixes: `claude`, `opencode`, `cursor`, `codex`, `gemini`, `kimi`, `pi`,
  `windsurf`, `copilot`, `kiro`.

### `SKILL.md` frontmatter

A skill is a directory whose `SKILL.md` carries YAML frontmatter:

```markdown
---
name: code-reviewer
description: Use when reviewing a diff or a pull request for correctness and style issues.
---

# Code reviewer

<the actual instructions>
```

`description` is load-bearing beyond documentation: `agentry emit triggers` writes each
skill's name → description into every target's memory file (`.claude/CLAUDE.md`,
`AGENTS.md`, `GEMINI.md`, …), which is how harnesses that don't auto-load skills learn
*when* to reach for yours. Write it as a **trigger condition** ("Use when …"), not a
summary of what the skill contains.

### The `agentry.yaml` descriptor

Add a descriptor at the source root only if your layout doesn't match the convention. It
says *where* things are; the type still dictates the shape.

```yaml
# <source-repo>/agentry.yaml
version: 1
provides:
  skill:
    # An explicit path.
    - name: code-reviewer
      path: packages/code-reviewer
  agent:
    # Or a glob — the name is derived from each match (file stem, or directory name).
    - glob: "ai/agents/*.md"
  mcp:
    - glob: "servers/*.json"
```

| Field | Type | Notes |
|---|---|---|
| `version` | int | Schema version. `1`. |
| `provides` | map of type → list of entries | Keys are `skill`, `agent`, `command`, `tool`, `hook`, `mcp`. |

Each `provides` entry is either `{name, path}` or `{glob}`, plus optional `requires`:

| Field | Type | Notes |
|---|---|---|
| `name` | str | Component name. Required with `path`; derived from the match with `glob`. |
| `path` | str | Path to the artifact, relative to the source root. |
| `glob` | str | Glob whose every match becomes a component. |
| `requires` | list of [Dependency](#requires-dependencies) | Components this one needs. |

`agentry.yml` works as a filename too.

#### `requires` (dependencies)

`requires` is how a component pulls in another. agentry closes the graph transitively, so a
consumer installing yours gets everything it needs.

| Field | Type | Notes |
|---|---|---|
| `type` | str | Component type of the dependency. |
| `name` | str | Its name. |
| `source` | str | A sibling source already declared by the consumer, or by an earlier dependency. |
| `url` | str | Fetch it from this git repo instead (agentry synthesizes the source). |
| `ref` | str | Pin that repo to a branch/tag/commit. **Pin it** — an unpinned `requires` re-resolves to a moving branch. |
| `subdir` | str | Where components live in that repo. |

```yaml
provides:
  skill:
    - name: release-notes
      path: skills/release-notes
      requires:
        - type: skill
          name: changelog-parser
          url: https://github.com/you/changelog-tools
          ref: v1.2.0
```

### `.apm/` packages work as-is

If your repo already ships an `.apm/` tree, agentry reads it directly — `.apm/skills/<name>/`,
`.apm/agents/<name>.agent.md`, `.apm/prompts/<name>.prompt.md` (mapped to agentry's
`command` type). `instructions` has no agentry equivalent and is skipped. No republishing
needed. See [architecture §4](architecture.md#4-source-repo-layout-convention-or-descriptor).

### Repos that are themselves the skill

Very common for third-party skills: the repo root *is* the component. Nothing to author —
the consumer resolves it directly with `path: "."`:

```bash
agentry add third-party/skill/third-party --path .
```

## Catalog entries

A catalog is a JSON index mapping a **bare name** to a source, so consumers can
`agentry add <name>` without knowing the URL. agentry ships a curated one
(`registry/repositories.json`), registered by default on `agentry init`.

### Don't hand-write it

`agentry catalog add-repo` writes the schema correctly and can discover the components for
you:

```bash
agentry catalog add-repo https://github.com/you/my-kit my-kit \
  --summary "What it provides, in one line." \
  --discover
```

`--discover` clones the repo, runs discovery, and fills `expose` from what it actually finds
— which is also a useful check that your layout works before anyone else depends on it.

### Index schema

```json
{
  "version": 1,
  "repositories": {
    "my-kit": {
      "summary": "One line on what this repo provides.",
      "source": { "type": "git", "url": "https://github.com/you/my-kit", "ref": "main" },
      "expose": [ { "type": "skill", "name": "code-reviewer" } ],
      "copy": false,
      "namespaced": true,
      "target_profiles": {}
    }
  },
  "targets": {}
}
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `version` | int | `1` | Schema version. |
| `repositories` | map name → entry | — | The catalog itself. |
| `targets` | map | `{}` | Shareable **driver overlays** — see [below](#publishing-a-driver-overlay). |

**Repository entry:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `summary` | str | `null` | One line, shown by `agentry search`. |
| `source` | object | — | `{type, url, path, ref, subdir}` — same shape as a `sources` entry, minus `name`. Defaults to `type: git`, `ref: main`. |
| `expose` | list | `null` | Curated components. Omit for a conventional-layout repo and let discovery do it. |
| `copy` | bool | `false` | Install this repo's components as real copies rather than symlinks. |
| `namespaced` | bool | `true` | Namespace destinations per repo, so two catalogs offering a same-named skill don't collide. |
| `target_profiles` | map | `{}` | Per-repo install-rule overrides, merged into the consumer's config at `agentry add` time. Use sparingly — it changes where files land in someone else's project. |

**`expose` entry:** `{type, name}`, plus `path` for an artifact discovery can't infer, or
`generate` for a component that installs by running its own CLI (see the
[configuration reference](config-reference.md#generate) — note that running third-party code
requires the consumer's explicit consent).

### Publishing a driver overlay

The `targets` block publishes named definitions of *how some agent installs each component
type* — the community-driver layer. A consumer runs `agentry target add <name>` and that
target becomes resolvable without hand-writing `target_profiles`.

```json
{
  "targets": {
    "myide": {
      "skill": { "strategy": "link", "dest": ".myide/skills/{name}" },
      "mcp":   { "strategy": "merge", "file": ".myide/config.json", "pointer": "mcpServers" }
    }
  }
}
```

Rule fields are documented under
[`target_profiles`](config-reference.md#target_profiles). `dest` and `file` must be
project-relative — agentry validates that on load and refuses to write outside the project
root — and `agentry target add` shows the consumer every destination and asks before
applying, because an overlay decides where files land in their repo.

### Getting into agentry's curated catalog

Two routes, both fine:

- **Open a PR** against [`registry/repositories.json`](https://github.com/OpenTechIL/agentry/blob/main/registry/repositories.json)
  with an entry generated by `agentry catalog add-repo` (say so in the PR body).
- **Open an issue** describing the repo and let a maintainer add it.

What reviewers look for: the repo installs cleanly (`agentry add` → `agentry sync` →
`agentry remove` round-trips), component names are the real ones discovery reports rather
than guesses, `summary` is one specific line, `target_profiles` is empty unless the repo
genuinely needs a non-default destination, and the source `ref` is a branch or tag you
intend to keep stable.

The `use-agentry` skill in this repo automates both routes via `gh` — install it with
`agentry add use-agentry` and ask your agent to publish the repo.
