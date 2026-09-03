# Configuration reference

Everything agentry reads is **project-local**. There is no user-level or global config, no
`~/.agentryrc`, and no XDG path: a checkout carries its complete agent setup, and running
`agentry` in a different project cannot change this one. agentry also reads from the
**current directory only** — it does not search upwards for a project root, so run it from
the directory holding `.agentry.yml`.

| File | Committed? | Written by | Purpose |
|---|---|---|---|
| `.agentry.yml` | yes | you (and `agentry` mutators) | Declared intent. Hand-editable; comments are preserved across writes. |
| `.agentry.lock` | yes | `agentry sync` / `update` | Resolved commit SHAs and content hashes. Timestamp-free, so it's byte-stable. |
| `.agentry/` | no (git-ignored) | `agentry sync` | Store: git clones, local-source links, cached catalog JSON, and `.manifest.json` (what is installed). |

Only `.agentry.yml` is authoritative. The lock is derived, and the store is a cache —
see [Troubleshooting](troubleshooting.md#recovering-a-broken-agentry-store).

## Top-level keys

| Key | Type | Default | Purpose |
|---|---|---|---|
| `version` | int | `1` | Schema version of this file. |
| `targets` | list of str | — | Which AI tools to install into. Built-in names below, or any key you define in `target_profiles`. |
| `sources` | list of [Source](#sources) | — | Where components come from. |
| `components` | list of [Component](#components) | — | What to install. |
| `repositories` | list of [Registry](#repositories) | — | Catalogs that `add` and `search` resolve bare names against. |
| `target_profiles` | map | — | Per-tool install rules. Defines new targets, or overrides a built-in driver. See [target_profiles](#target_profiles). |
| `transform` | [TransformConfig](#transform) | `null` | How to invoke your agent CLI for content transforms and `emit --agent`. |
| `hashing` | [HashingConfig](#hashing) | `{normalize_line_endings: true}` | How local-source content is hashed. |

Built-in `targets` values: `claude`, `opencode`, `cursor`, `codex`, `gemini`, `windsurf`,
`kimi`, `copilot`, `kiro`, plus the tool-neutral `agents` (writes `.agents/skills/…`).

## `sources`

A source is a git repo or a directory on this machine.

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | — | How components refer to it. |
| `type` | `git` \| `local` | — | `agentry source add --local` sets `local`. |
| `url` | str | `null` | Required for `git`. Allowed transports: `https://`, `http://`, `ssh://`, `git://`, `file://`, `user@host:path`, or a local path. Anything else is refused — git's `ext::` transport would execute a shell command at clone time. |
| `path` | str | `null` | Required for `local`. |
| `ref` | str | `"main"` | Branch, tag or commit for `git` sources. Resolved to a concrete SHA in the lock. |
| `subdir` | str | `null` | Components live under this subdirectory (monorepo support). Must be relative and inside the repo. |

## `components`

| Field | Type | Default | Notes |
|---|---|---|---|
| `source` | str | — | A `name` from `sources`. |
| `type` | str | — | One of `skill`, `agent`, `command`, `tool`, `hook`, `mcp`. |
| `name` | str | — | The component's name in that source. `agentry list` shows the real names. |
| `enabled` | bool | `true` | `false` uninstalls on the next sync but keeps the entry (`agentry disable`). |
| `targets` | list of str | `null` | Install only into these targets. `null` means every target in the project's `targets`. |
| `path` | str | `null` | Explicit path to the artifact within the source, overriding discovery. Must be relative and inside the repo. |
| `generate` | [GeneratorSpec](#generate) | `null` | The component installs itself by running its own CLI. |
| `transform` | `{provider, prompt}` | `null` | Materialize through a content provider instead of a live symlink. `provider` is `strip-frontmatter` or `agent`. |

Its identifier on the CLI is `<source>/<type>/<name>` — that's what `add`, `remove`, `why`
and `deps` take.

**There is no `strategy` key on a component.** The install strategy belongs to the
(target, type) rule in [`target_profiles`](#target_profiles). A `strategy:` written under a
`components:` entry is silently ignored.

### `generate`

For tools that ship no symlinkable artifact and build their files at install time.

| Field | Type | Notes |
|---|---|---|
| `setup` | list of argv lists | Commands to run first. |
| `command` | argv list | The installer command. |
| `produces` | list of str | Paths it creates. This is the removal contract: `agentry remove` deletes exactly these, nothing else. Each must be relative and inside the project. |

Running third-party code is gated: `agentry sync --allow-run`, or a persisted
`agentry trust <source>` decision (pinned to the source's SHA, so trust drops if it moves).
Removal never runs code and is always allowed.

## `repositories`

Catalogs — the "artifactory" front end that lets `agentry add <bare-name>` work.

| Field | Type | Notes |
|---|---|---|
| `name` | str | Local label for the catalog. |
| `location` | str | An `http(s)` URL or a path to a JSON index. URLs are cached under `.agentry/repositories/`; fetches time out and are size-capped. |

`agentry init` registers agentry's curated catalog by default. Opt out with
`--no-default-catalog`. The index format is documented in [Authoring](authoring.md#catalog-entries).

## `target_profiles`

`target_profiles[<tool>][<component-type>]` is one **rule** saying how that type installs
for that tool. Two uses:

- **Define a brand-new target.** Targets are open strings, so a tool with no built-in
  driver becomes supported by writing rules for it — no fork, no plugin, no release wait.
- **Override a built-in driver.** Rules are deep-merged *over* the driver, so you can change
  one type (e.g. skills to `copy`) and inherit the rest.

| Field | Type | Required for | Notes |
|---|---|---|---|
| `strategy` | `link` \| `copy` \| `merge` \| `link+merge` \| `generate` | always | How the artifact lands. |
| `dest` | str | `link`, `copy`, `link+merge` | Destination path template — see [template variables](#dest-template-variables). Must be relative and inside the project. |
| `file` | str | `merge`, `link+merge` | Config file to merge into. JSON or TOML, by extension. Must be relative and inside the project. |
| `pointer` | str | `merge`, `link+merge` | Top-level key in that file that agentry owns. |
| `rewrite_from` / `rewrite_to` | str | optional, `link+merge` | Rewrite a command-path prefix in the merged fragment so symlinked scripts resolve. Set both or neither. |

`dest` and `file` are validated as project-relative on load and re-checked when they are
joined onto the project root, because a catalog can publish them.

### `dest` template variables

Which placeholders expand depends on the strategy:

| Variable | `link` / `copy` | `link+merge` | Value |
|---|---|---|---|
| `{name}` | yes | yes | The component name. |
| `{source}` | — | yes | The source's `name`. |
| `{repo}` | — | yes | Repo basename from the source URL/path, `.git` stripped. |
| `{ref}` | — | yes | The requested git ref, with `/` flattened to `-`. |

`link+merge` gets the extra three so a profile can namespace linked directories per repo and
ref — `.claude/hooks/agentry/{repo}@{ref}/{name}` — rather than colliding on `{name}` alone.
`rewrite_to` expands the same set, and substitution is literal replacement (not
`str.format`), so a template may safely contain `${SHELL_STYLE}` variables for the agent to
resolve at runtime.

## `transform`

| Field | Type | Notes |
|---|---|---|
| `command` | argv list | Your agent CLI. Receives the prompt on stdin and must print only the rewritten content. Used by `agentry emit agents-md --agent` and by `transform.provider: agent`. |

Gated by `--allow-transform`, with a diff preview and confirmation (`--yes` for CI).

## `hashing`

| Field | Type | Default | Notes |
|---|---|---|---|
| `normalize_line_endings` | bool | `true` | Normalize CRLF before hashing local-source content, so a `sha256:` pin matches on Windows and Unix and `sync --frozen` doesn't report phantom drift from a checkout's line endings. |

## Annotated example

```yaml
version: 1

# Which tools to install into. Add `agents` for the tool-neutral .agents/skills layout.
targets: [claude, cursor, myide]

sources:
  # A git repo, pinned to a branch (resolved to a SHA in .agentry.lock).
  - name: team-skills
    type: git
    url: https://github.com/org/team-skills
    ref: main
  # A monorepo: components live under packages/agent-kit/.
  - name: platform
    type: git
    url: git@github.com:org/platform.git
    ref: v2.4.0
    subdir: packages/agent-kit
  # A directory on this machine — handy for a skill you're still writing.
  - name: scratch
    type: local
    path: ../my-skills

components:
  - source: team-skills
    type: skill
    name: code-reviewer
  # Only install this one into Claude, not every target.
  - source: team-skills
    type: mcp
    name: github
    targets: [claude]
  # Parked: stays in the file, uninstalled on the next sync.
  - source: platform
    type: agent
    name: release-bot
    enabled: false
  # Points at an artifact discovery wouldn't find on its own.
  - source: scratch
    type: skill
    name: draft
    path: work-in-progress/draft
  # Strips YAML frontmatter on the way out, for a tool that chokes on it.
  - source: team-skills
    type: skill
    name: linter
    transform:
      provider: strip-frontmatter

repositories:
  - name: agentry
    location: https://raw.githubusercontent.com/OpenTechIL/agentry/refs/heads/main/registry/repositories.json

target_profiles:
  # A tool with no built-in driver — supported entirely from config.
  myide:
    skill:
      strategy: link
      dest: .myide/skills/{name}
    mcp:
      strategy: merge
      file: .myide/config.json
      pointer: mcpServers
  # Override one rule on a built-in driver; everything else is inherited.
  claude:
    skill:
      strategy: copy               # commit real files instead of symlinks
      dest: .claude/skills/{name}

transform:
  command: [claude, -p]

hashing:
  normalize_line_endings: true
```

Verify any config with `agentry doctor` before syncing — it reports undefined targets,
components a source doesn't provide, unset `${VAR}` placeholders, type/target combinations
that install nowhere, and drift. See [Troubleshooting](troubleshooting.md#reading-agentry-doctor).
