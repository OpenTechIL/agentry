# Troubleshooting

The failures people actually hit, what causes them, and the fix.

## The `agy` command runs the wrong tool

**Symptom.** `agy` prints help for something that isn't agentry, or Homebrew tells you:

```
The following agentry executables are shadowed by other commands earlier in your PATH:
  agy (shadowed by /usr/local/bin/agy)
```

**Cause.** `agy` is also the command for **Google's Antigravity CLI** (the successor to
Gemini CLI). agentry keeps `agy` for backwards compatibility, so when both tools are
installed, whichever comes first on `PATH` wins — silently.

**Fix.** Use the unambiguous names. agentry installs three, all the same program:

| Name | Use it |
|---|---|
| `agentry` | Canonical. Prefer this in scripts, CI, docs and issue reports. |
| `agyx` | Short alias that cannot collide. |
| `agy` | Legacy alias. May be Antigravity's on your machine. |

Check what you have:

```bash
which -a agy        # every agy on PATH, in resolution order
agentry doctor      # reports the conflict as a check
agentry --version   # confirms you are talking to agentry
```

agentry prints a one-line notice to stderr when it is invoked as `agy` and finds a
different `agy` earlier on `PATH`. Silence it with `AGENTRY_NO_COLLISION_WARN=1`.

## `No .agentry.yml found in <dir>`

**Cause.** agentry reads its config from the **current directory only** — there is no
upward search for a project root the way `git` or `npm` walk up the tree.

**Fix.** Run it from the directory that holds `.agentry.yml` (your repo root):

```bash
cd "$(git rev-parse --show-toplevel)" && agentry status
```

If the project has never been set up, `agentry init` creates the file. There is no
user-level or global config — configuration is per project, by design, so a checkout
carries everything needed to reproduce its agent setup.

## `sync` fails creating symlinks on Windows

**Cause.** The default install strategy is a **symlink**, and Windows only permits
unprivileged symlink creation when
[Developer Mode](https://learn.microsoft.com/en-us/windows/apps/get-started/enable-your-device-for-development)
is enabled. Without it you get `OSError: [WinError 1314] A required privilege is not held
by the client`. agentry does **not** silently fall back to copies.

**Fix**, in order of preference:

1. Enable Developer Mode (Settings → System → For developers). One-time, no admin needed
   for subsequent runs.
2. Switch the affected component types to `strategy: copy` in `target_profiles`. A copy is
   a real file, so it commits and travels with the repo — the trade-off is that edits in the
   store no longer show up live, and you re-run `agentry sync` to pick them up.
3. Run the shell as administrator.

Strategy is a property of a **(target, component type)** rule, not of an individual
component — there is no `strategy:` key on a `components:` entry, and one written there is
silently ignored:

```yaml
target_profiles:
  claude:
    skill:
      strategy: copy
      dest: .claude/skills/{name}
    agent:
      strategy: copy
      dest: .claude/agents/{name}.md
```

Overriding only `strategy` keeps the target's built-in [format checks](config-reference.md#expect).
If you switch an OpenCode or Gemini type to `copy`, you still get its
[format warnings](#a-component-may-not-work-on-its-target-format-warnings). Add `expect: {}` to
the rule only if your override also changes the format the tool reads.

## `<path> already exists and is not managed by agentry`

**Cause.** Working as intended: agentry refuses to overwrite anything it did not install.
Something else — you, another tool, a previous manual copy — owns that path.

**Fix.** Inspect it, then either delete it (if it really was an unmanaged copy of the same
thing) or point agentry elsewhere via the target's `dest` template. `agentry why <ref>`
shows exactly where agentry wants to install a component and why.

## `<path> is a symlink agentry does not manage`

Same principle: the path is a symlink, but it does not resolve into agentry's `.agentry/`
store, so agentry will not touch it. Remove it by hand if it is stale.

## `<file> is not valid JSON` — a component was not merged

**Symptom.** `sync` (or `add`) prints a warning like this and the component is not installed
for that target:

```text
demo/mcp/github: not merged into its config — .vscode/mcp.json is not valid JSON (line 2, column 3: Expecting property name enclosed in double quotes) — left unchanged; fix it (comments and trailing commas are not supported), then re-run sync
```

**Cause.** agentry merges `mcp` and `hook` components into config files such as `.mcp.json`,
`.vscode/mcp.json`, `.claude/settings.json` and `.codex/config.toml`, and it reads them as
strict JSON or TOML. Some tools accept looser files. VS Code, for example, reads
`.vscode/mcp.json` as JSONC, so `//` comments and trailing commas are common there. agentry does
not support JSONC. Instead of guessing, it leaves the file untouched and skips **only the
components that merge into that file**; everything else still installs. A plugin-style hook
skips its script symlink too, so nothing is left half-installed.

The same applies when the file is not valid TOML (`<file> is not valid TOML (…)`), is not UTF-8
text (`<file> is not UTF-8 text`), or has something other than an object at the top level
(`<file> is not a JSON object`).

`agentry status` shows the affected components as `missing` and prints the file problem under
the table. `agentry doctor` repeats it on each affected component, and `agentry doctor --strict`
fails on it:

```text
! drift  demo/mcp/github → copilot: missing (.vscode/mcp.json is not valid JSON (line 2, column 3: Expecting property name enclosed in double quotes) — left unchanged; fix it (comments and trailing commas are not supported), then re-run sync)
```

**Fix.** Make the file valid. Delete the comments and trailing commas, starting at the line and
column in the message, then run `agentry sync`. If you need to keep a commented file, disable the
components that merge into it (`agentry disable <ref>`) and maintain that file by hand.

If you remove a component while the file is broken, the warning says "not removed from its
config" instead. agentry keeps its record of the entry, and the first sync after you fix the
file removes it.

## A config entry was not merged (`mcp` and `hook` components)

`mcp` and `hook` components add named entries to a shared config file such as `.mcp.json`,
`.claude/settings.json` or `.vscode/mcp.json`. agentry only changes entries it added itself, and
it can tell whether an entry on disk is still the one it wrote. When it can't merge safely, it
leaves your entry alone and prints one of the warnings below. `sync` still exits 0.
- **On first install,** a conflict skips the whole component for that file, so nothing is
  half-installed.
- **Once the component is installed,** only the conflicting entry is skipped. The component's
  other entries keep updating.

Where a warning suggests `agentry keep`, it names the exact command, scoped as narrowly as
possible. It names only the targets where the entry drifted (for example `claude:github`). It
uses the bare `<key>` only when the entry drifted on every target the component writes it to,
which includes a component with a single target.

To find these later:
- `agentry status` shows the row as `missing` (or `drift`) and prints the reason under the table.
- `agentry doctor` repeats the reason, and `agentry doctor --strict` fails on it, so CI notices.

Running `agentry sync` again does not fix these. Apply the fix first.

### `already has '<pointer>.<key>' (not added by agentry)`

```text
<ref>: <file> already has '<pointer>.<key>' (not added by agentry) — left unchanged; <ref> was not merged into <target>. Rename or remove your entry, or disable <ref>, then re-run sync.
```

**Cause.** The file already has an entry with that name, for example an MCP server called
`github` that you added by hand, and it differs from the component's entry. agentry won't
overwrite something it didn't create. If the component was already installed and the entry is a
new one from upstream, the message says `'<key>' was not merged into <target>`. Only that entry is
skipped in that case.

**Fix.** Choose one, then run `agentry sync`:
- **Keep both.** Rename your entry.
- **Use the component's entry.** Delete yours.
- **Keep yours and drop the component.** Run `agentry disable <ref>`.

### `already has an identical '<pointer>.<key>'`

```text
<ref>: <file> already has an identical '<pointer>.<key>' (not added by agentry) — using yours; agentry will never remove it.
```

**Cause.** Your entry is exactly what the component ships. agentry counts the component as
installed (`status` shows `ok`) but treats the entry as yours. You see this once, on the first
sync.

**Fix.** Usually nothing. Note that `agentry remove` and `agentry disable` will leave this entry
in place. If you want agentry to manage it, delete it and run `agentry sync`. agentry then
creates and owns it.

### `is your entry (it matched <ref> earlier, but upstream has changed)`

```text
<ref>: '<pointer>.<key>' in <file> is your entry (it matched <ref> earlier, but upstream has changed) — keeping yours. Delete it to take <ref>'s version, or run `agentry keep <ref> [<target>:]<key>` to keep it.
```

**Cause.** This follows the previous case. The source has since updated the entry, and yours no
longer matches it. agentry still won't change your entry. Only this entry is held back, and the
component's other entries keep updating. `status` shows the row as `drift`.

**Fix.** To take the new version, delete your entry and run `agentry sync`. To keep yours and
stop the warning, run the `agentry keep …` command from the message.

### `kept, but no entry on <target>`

```text
demo/mcp/servers: 'mcpServers.github' in .cursor/mcp.json — kept, but no entry on cursor; nothing installed. Add yours, remove it from `keep:` to let agentry install it, or narrow keep to `<target>:<key>`.
```

When this happens for two or more keys, sync prints one summary line instead, and
`agentry doctor` lists each one:

```text
2 keys in keep: have no entry on one or more targets and were not installed — run `agentry doctor` for the list
```

**Cause.** The component's [`keep:`](config-reference.md#keep) list has a bare key such as
`github`, which applies to every target, including targets you added later. On this target
there's no entry with that name, and agentry never creates a kept entry. The usual causes are a
target you added after keeping the key, or a kept entry you deleted. `status` shows the row as
`missing`, and `doctor --strict` fails on it.

**Fix.** Choose one:
- **Add your own entry** to that file.
- **Let agentry install it.** Remove the key from `keep:`.
- **Scope the keep.** Change it to `<target>:<key>` (for example `claude:github`) so it covers
  only the targets where you want your own value.

### `is your entry (no longer in keep:)`

```text
demo/mcp/servers: 'mcpServers.github' in .mcp.json is your entry (no longer in keep:) — left unchanged. Delete it and re-run sync to take demo/mcp/servers's version.
```

**Cause.** You removed this entry from the component's [`keep:`](config-reference.md#keep) list,
but your own value is still in the file. agentry still treats it as yours and leaves it alone.
The component's other entries keep updating.

**Fix.** To hand it back to agentry, delete the entry and run `agentry sync`. To keep it after
all, add it back with `agentry keep <ref> [<target>:]<key>`.

### `'<pointer>' in <file> is a <type>, not an object`

```text
<ref>: '<pointer>' in <file> is a list, not an object — not merged. Fix the file by hand, then re-run sync.
```

**Cause.** The section agentry merges into (for example `mcpServers` or `servers`) exists but
holds a list, string, number, boolean or `null` instead of an object. Every component that
targets that section is skipped, and agentry never replaces the section.

**Fix.** Edit the file so that the section is an object, for example `"mcpServers": {}`. Keep
anything you need from the old value. Then run `agentry sync`.

### `is already provided by <other>`

```text
<ref>: '<pointer>.<key>' in <file> is already provided by <other> — skipped. Disable one of them or rename the entry.
```

**Cause.** Two enabled components ship an entry with the same name for the same file. Only one
of them can have it. Only that entry is skipped. The component's other entries still install.

**Fix.** Run `agentry disable` on whichever component you don't want. If you need both, rename
the entry in one of the sources, or ask its author to.

### `was edited after agentry installed it` (status shows `drift`)

```text
<ref>: '<pointer>.<key>' in <file> was edited after agentry installed it — keeping your version. Delete it and re-run sync to restore agentry's, or run `agentry keep <ref> [<target>:]<key>` to keep yours.
```

For example, with `claude` and `copilot` as targets and the entry edited only in `.mcp.json`:

```text
demo/mcp/servers: 'mcpServers.github' in .mcp.json was edited after agentry installed it — keeping your version. Delete it and re-run sync to restore agentry's, or run `agentry keep demo/mcp/servers claude:github` to keep yours.
```

If you edited it in both `.mcp.json` and `.vscode/mcp.json`, each warning suggests the bare key:

```text
demo/mcp/servers: 'mcpServers.github' in .mcp.json was edited after agentry installed it — keeping your version. Delete it and re-run sync to restore agentry's, or run `agentry keep demo/mcp/servers github` to keep yours.
demo/mcp/servers: 'servers.github' in .vscode/mcp.json was edited after agentry installed it — keeping your version. Delete it and re-run sync to restore agentry's, or run `agentry keep demo/mcp/servers github` to keep yours.
```

**Cause.** You changed an entry that agentry installed. agentry keeps your edit and stops
updating that entry from upstream. The warning repeats on every sync until you resolve it.
`agentry doctor` lists every such entry with its file and component (see the
[example below](#was-recorded-by-an-older-agentry-and-differs-from-the-current-source)).

**Fix.** Choose one:
- **Keep your edit.** Run the `agentry keep …` command from the message. This adds the key to
  the component's [`keep:`](config-reference.md#keep) list, so agentry stops warning and never
  writes or removes the entry. `status` shows it as `ok (kept)`.
- **Go back to agentry's version** and receive upstream updates again. Delete the entry and run
  `agentry sync`.

If a hook pack's events depend on each other (for example `PreToolUse` and `PostToolUse` scripts
sharing state), keep or delete them together. agentry handles each entry independently.

### `was recorded by an older agentry and differs from the current source`

```text
demo/mcp/github: 'mcpServers.github' in .mcp.json was recorded by an older agentry and differs from the current source — keeping it. If you didn't edit it, delete it and re-run sync to get the current version, or run `agentry keep demo/mcp/github github` to keep yours.
```

`agentry status` shows the row as `drift`. To get the full list of affected entries, run
**`agentry doctor`**. It prints one line per entry, giving the file, the `pointer.key`, the
component that installed it, and the fix. It also lists entries you edited by hand in the same
format, and `--strict` fails on any of them:

```text
! drift  .mcp.json: 'mcpServers.github' (from demo/mcp/github → claude) was recorded by an older agentry and differs from the current source — delete it and run `agentry sync` to get the current version, or run `agentry keep demo/mcp/github github` to keep yours
! drift  .mcp.json: 'mcpServers.slack' (from demo/mcp/slack → claude) was edited by hand — delete it and run `agentry sync` to get the current version, or run `agentry keep demo/mcp/slack slack` to keep yours
```

**Cause.** You upgraded from an agentry version that didn't record what it wrote into config
files. On the first sync after the upgrade, agentry keeps managing an entry only if it still
matches what the component ships today. An entry that doesn't match might be one you edited, or
it might be out of date, so agentry treats it as yours. It won't overwrite or remove it. Every
such entry is affected if the source changed upstream before that first sync, for example
because you ran `agentry update` first. That's why the upgrade advice is to run a plain
`agentry sync` once before `agentry update`. If you run `update` while the old record is still
in place, agentry reminds you:

```text
manifest from an older agentry: run `agentry sync` before `agentry update` so installed entries can be verified
```

**Fix.** Go through `agentry doctor`'s list:
- **For each entry you edited and want to keep,** run the `agentry keep …` command doctor prints.
  agentry then stops warning about it and never touches it.
- **Delete every other entry** and run `agentry sync`. agentry installs the current version and
  manages it from then on.

### `was edited after install — left in place on removal`

```text
<ref>: '<pointer>.<key>' in <file> was edited after install — left in place on removal; delete it by hand if unused, or run `agentry keep <ref> [<target>:]<key>` to keep it as yours.
```

**Cause.** You removed or disabled a component whose entry you had edited. agentry treats the
edited entry as yours, so it doesn't delete it.

**Fix.** If you no longer need the entry, delete it from the file by hand. If you want it, leave
it. This warning appears only once, when the component is removed. To record the entry as yours
in `.agentry.yml`, in case you enable the component again later, run the `agentry keep …` command
from the message.

If you remove a component whose entry agentry couldn't verify after an upgrade (see
[the previous entry](#was-recorded-by-an-older-agentry-and-differs-from-the-current-source)),
you get a variant of this warning:

```text
<ref>: '<pointer>.<key>' in <file> could not be verified as agentry's (recorded by an older agentry) — left in place on removal; delete it by hand if unused, or run `agentry keep <ref> [<target>:]<key>` to keep it as yours.
```

The fix is the same.

### `was installed by an older agentry with link+merge`

```text
<ref>: '<pointer>.<key>' in <file> was installed by an older agentry with link+merge and can't be verified — left in place on removal; its commands may point at the removed <link path>. Delete them by hand if unused.
```

**Cause.** You disabled or removed a plugin-style hook pack. That's the kind that links a scripts
folder into place and merges hook commands that call those scripts. An older agentry installed
it, and agentry can no longer work out what it wrote: the source or the file is gone, or the
target no longer installs that type this way. So it removes the scripts folder but leaves the
hook entry, whose commands may now point at a path that no longer exists. Your agent may then
report a failing hook. While the component is still listed in `.agentry.yml`, even disabled,
agentry can check the entry, and it removes a matching one cleanly without this warning.

**Fix.** Open `<file>` and delete the hook entries under `<pointer>.<key>` that reference
`<link path>`. Keep any hook commands you added yourself.

## A component may not work on its target (format warnings)

agentry puts a component where each tool looks for it, but it doesn't translate the content.
Some tools expect a different format, so agentry checks for known mismatches. It still installs
the component as-is, prints a `may not work in <target>` warning, and `agentry doctor` lists it
under the `format` category (`doctor --strict` fails). Each warning names what the target expects
and what's wrong.

The general fixes:
- **For `mcp` and `hook` components, ship a per-target variant.** Put a
  `<base>-<target>.json` file next to the original in the same source, for example
  `mcp/github-opencode.json` next to `mcp/github.json`, and enable both. On that target only the
  variant installs, and every other target keeps the original. `agentry why <ref>` shows which
  file each target gets:

  ```text
  demo/mcp/github
  └── installs to
      ├── claude  .mcp.json:mcpServers  ok
      └── opencode  replaced by demo/mcp/github-opencode
  demo/mcp/github-opencode
  └── installs to
      └── opencode ← github-opencode.json (variant; replaces github.json)  opencode.json:mcp  ok
  ```

- **For other types (such as commands), ship a second component limited with `targets:`.**
  Restrict the original to the targets it works on, and limit the target-specific copy to the
  problem target ([`targets`](config-reference.md#components) on each entry).
- **Override the rule** under [`target_profiles`](config-reference.md#target_profiles). If your
  override changes the format the tool reads, add `expect: {}` to drop the check.

### OpenCode: an MCP server in Claude's shape

```text
demo/mcp/github: may not work in opencode (expects `type: "local"` with `command` as one array and `environment`): 'github' has no 'type'; 'github'.command is a string, not an array; 'github' uses 'args', which is not accepted; 'github' uses 'env', which is not accepted — installed as-is. Ship an opencode-specific mcp, or override mcp for opencode under target_profiles
```

**Cause.** OpenCode's `opencode.json` wants `type: "local"` (or `"remote"`), `command` as one
array with the arguments included, and `environment` instead of `env`. It rejects unknown fields
such as `args`. A Claude-style `{"command": "npx", "args": [...], "env": {...}}` entry doesn't
start. A `type: "http"` entry gets the same warning (`'docs'.type is 'http', not one of local,
remote`).

**Fix.** Ship an OpenCode variant, `mcp/github-opencode.json`, in the same source and enable
it alongside `github`:

```json
{"github": {"type": "local", "command": ["npx", "-y", "gh-mcp"], "environment": {"TOKEN": "x"}}}
```

OpenCode then gets only the variant, and the other targets keep `mcp/github.json`.

### Gemini: a command that isn't a `.toml` file

```text
demo/command/review: may not work in gemini (expects a TOML command file with a `prompt` field): 'review.md' is not a .toml file — installed as-is. Ship a gemini-specific command, or override command for gemini under target_profiles
```

**Cause.** Gemini CLI reads custom commands from TOML files with a `prompt` field. A Markdown
command is linked in under a `.toml` name, but its content is still Markdown, so Gemini can't
load it.

**Fix.** Ship a `review.toml` command (with `prompt = "…"`) as a separate component limited to
`targets: [gemini]`, and limit the Markdown one to the other targets.

### Gemini: hook events with Claude names

```text
demo/hook/guard: may not work in gemini (expects Gemini hook event names (BeforeTool, AfterTool, SessionStart, …)): unknown event(s) PreToolUse — installed as-is. Ship a gemini-specific hook, or override hook for gemini under target_profiles
```

**Cause.** Gemini names its hook events differently, for example `BeforeTool` and `AfterTool`
instead of Claude's `PreToolUse` and `PostToolUse`. A hook under a Claude event name is merged
but never fires.

**Fix.** Ship a Gemini variant with Gemini event names, for example `hooks/guard-gemini.json`
with `{"BeforeTool": [...]}`, in the same source, and enable it alongside `guard`. Gemini then
gets only `BeforeTool`, and Claude keeps `PreToolUse`.

### Gemini: an HTTP MCP server read as SSE

```text
demo/mcp/docs: may not work in gemini (expects `httpUrl` for HTTP servers (`url` means SSE)): 'docs'.type is 'http' — Gemini reads `url` as SSE; use `httpUrl` for streamable HTTP — installed as-is. Ship a gemini-specific mcp, or override mcp for gemini under target_profiles
```

**Cause.** Gemini has no `type` field for MCP servers. It treats `url` as an SSE endpoint and
`httpUrl` as streamable HTTP. A Claude-style `{"type": "http", "url": "…"}` entry (or
`type: "streamable-http"`) quietly connects over SSE, which fails against an HTTP-only server.

**Fix.** Ship a Gemini variant, `mcp/docs-gemini.json`, that uses `httpUrl`, and enable it
alongside `docs`:

```json
{"docs": {"httpUrl": "https://example.com/mcp"}}
```

## A summary line instead of individual warnings

To keep output readable, `sync`, `install`, `update` and `add` collapse a warning that occurs two
or more times into one line:

```text
! 2 components skipped on targets that don't support their type — run with `--verbose` for the list (expected; see `agentry target drivers`)
! 2 merged entries differ from their source and were kept as-is — run `agentry doctor` for the list
```

**Skipped components** are normal when your targets have different capabilities. Cursor has no
skills, for example, so with `claude` and `cursor` as targets every skill is skipped for Cursor.
`agentry doctor` doesn't list these skips and `doctor --strict` doesn't fail on them. To see each
one, rerun with `-v`:

```text
! demo/skill/a: target 'cursor' does not support skill — skipped
! demo/skill/b: target 'cursor' does not support skill — skipped
```

`agentry target drivers` lists the built-in targets. The
[capability table in the README](https://github.com/OpenTechIL/agentry/blob/main/README.md#supported-agents)
shows which component types each target installs.

**Component targets that aren't active** (a component's `targets:` names a target the project
doesn't list) are summarized too:

```text
! 2 component targets aren't active in this project and were skipped — run `agentry doctor` for the list
```

See [`target '<name>' is not in the project's targets — skipped`](#target-name-is-not-in-the-projects-targets-skipped).

**Format mismatches** have their own summary:

```text
! 4 components may not work on their target (format mismatch) — run `agentry doctor` for the list
```

It counts components, not warnings: one component that fails on two targets counts once.
`agentry doctor` lists each (component, target) case under `format`. See
[format warnings](#a-component-may-not-work-on-its-target-format-warnings).

**Merged entries that differ** are entries you edited by hand, or entries an older agentry
recorded that no longer match their source. `agentry doctor` lists each one with its file, key
and fix. See
[`was recorded by an older agentry and differs from the current source`](#was-recorded-by-an-older-agentry-and-differs-from-the-current-source).

To print every warning in full, as earlier versions did, pass `-v` / `--verbose` to any of those
commands. Warnings that need action always print individually: refused merges, unparseable
files, the trust gate, and unset `${VAR}`s.

## Reading `agentry doctor`

`doctor` is a read-only preflight. It exits non-zero on **errors** and, with `--strict`,
on warnings too — which makes it a good CI gate.

| Category | Level | Meaning |
|---|---|---|
| `target` | error | A target in `targets:` has no built-in driver and no `target_profiles` entry. |
| `source` | error | A component references a source that isn't declared. |
| `component` | error | A source doesn't actually provide the component you enabled. Run `agentry list` for the real names. |
| `type` | warn | No active target installs this component type, so it goes nowhere. |
| `env` | warn | A merge fragment references `${VAR}`, which is unset. agentry ships the placeholder; your agent resolves it at runtime. |
| `drift` | warn | On-disk state doesn't match config/lock. Run `agentry sync`. |
| `command` | warn | The `agy` alias on `PATH` belongs to another tool (see above). |

## An undefined target during `sync`

**Symptom.** `no driver for target 'foo' — define it under target_profiles, or install a
shared driver overlay (agentry target list)`.

**Fix.** Either write the target yourself (`target_profiles` in `.agentry.yml` — see the
[configuration reference](config-reference.md#target_profiles)), or install an overlay a
catalog publishes:

```bash
agentry target list          # what overlays are available
agentry target add foo       # shows the destinations, then asks before applying
```

`target add` prints every destination the overlay will write to and prompts for
confirmation, because an overlay arrives over the network and dictates where files land.
Pass `--yes` in CI once you've reviewed it.

## `target '<name>' is not defined` — nothing was changed

**Symptom.** A command exits 1 with:

```text
target 'cladue' is not defined — did you mean 'claude'? Nothing was changed. Fix `targets:` in .agentry.yml, define it under target_profiles, or install a driver overlay (`agentry target list`, then `agentry target add cladue`).
```

If the typo is in one component's `targets:` list, the message starts with that component:

```text
demo/skill/a: target 'claud' is not defined — did you mean 'claude'? Nothing was changed. Fix `targets:` in .agentry.yml, define it under target_profiles, or install a driver overlay (`agentry target list`, then `agentry target add claud`).
```

**Cause.** A name in `targets:` has no built-in driver, no `target_profiles` entry and no
installed overlay, which is usually a typo. agentry stops before touching anything. Your
installed files stay as they are, and a command that would edit `.agentry.yml` (`add`,
`enable`, `keep`, …) doesn't save its change. Earlier versions treated the unknown name as an
empty target and uninstalled the components of the target you meant.

**Fix.** Correct the name in `targets:` (the message suggests the closest one), then re-run the
command. If the name is a real tool agentry doesn't ship, define it under
[`target_profiles`](config-reference.md#target_profiles) or install a catalog overlay with
`agentry target add <name>`. `agentry catalog add` still works while the config is in this
state. Removing the component whose `targets:` holds the typo also works.

`agentry target use` gives the same kind of hint for a name that doesn't exist:

```text
'cursr' isn't a built-in target and has no target_profiles entry. Did you mean 'cursor'? Built-ins: claude, opencode, cursor, codex, gemini, windsurf, kimi, copilot, kiro, agents. For a community driver overlay, run `agentry target add cursr` first, then `agentry target use cursr`.
```

## `target '<name>' is not in the project's targets — skipped`

**Symptom.** sync, status or doctor prints:

```text
! demo/skill/a: target 'opencode' is not in the project's targets — skipped. Add it with `agentry target use opencode`, or remove it from this component.
```

**Cause.** The component's own `targets:` lists a target that the project's `targets:` doesn't.
A component's `targets:` can only narrow the project's list, so agentry skips that target. If
an earlier agentry version had installed the component there, the next sync removes it and
prints each removal (`- link …`, `- merge …`). Only agentry's own entries are removed: entries
you added by hand to that tool's config stay. `agentry doctor` reports it as a `target`
warning, so `doctor --strict` fails. `agentry target list` shows these under **Referenced but
not active**:

```text
Referenced but not active (components skip these)
  opencode ← demo/skill/a, demo/mcp/github — activate with `agentry target use opencode`
```

When two or more are skipped, sync prints one summary line instead, and `agentry doctor` lists
each one:

```text
! 2 component targets aren't active in this project and were skipped — run `agentry doctor` for the list
```

A `generate` component whose targets are all inactive is skipped the same way, and its
generator doesn't run. `agentry keep <ref> <target>:<key>` also refuses an inactive target:

```text
target 'opencode' isn't in the project's targets — add it with `agentry target use opencode`, or keep the bare '<key>' for the targets demo/mcp/github does install into.
```

**Fix.** If you do want that tool, activate it for the project with
`agentry target use <name>`. Otherwise remove the name from the component's `targets:`.

## `--frozen` fails in CI

`sync --frozen` installs strictly from `.agentry.lock` and refuses to resolve anything new.
It fails when:

- **a source isn't in the lock** — someone edited `.agentry.yml` without running `agentry sync`
  locally. Run it and commit the updated lock.
- **the lock is out of date relative to config** — same fix.
- **the store is missing** — `.agentry/` is git-ignored, so a fresh CI checkout has to clone.
  That's expected and `--frozen` handles it; if it fails here, the pinned SHA is gone from
  the remote (a force-push or deleted branch). Re-run `agentry update <source>`.

## Recovering a broken `.agentry/` store

`.agentry/` is a cache: clones, local-source symlinks, cached catalog JSON, and the install
manifest. Nothing in it is authoritative except the manifest's record of what's installed.

```bash
agentry remove <ref>   # for anything you want cleanly uninstalled first
rm -rf .agentry        # drop the store
agentry sync           # re-clone and reinstall from .agentry.yml + .agentry.lock
```

Deleting the store without removing components first leaves the installed symlinks dangling;
`agentry sync` re-creates them, so run it straight after.

## macOS Gatekeeper / Windows SmartScreen blocks the binary

The release binaries are signed with [cosign](https://github.com/sigstore/cosign) for
verifiable provenance, but they are **not** OS-notarized, so first run triggers a warning.

- **macOS**: System Settings → Privacy & Security → "Open Anyway".
- **Windows**: More info → Run anyway.

To verify provenance yourself, see
[packaging/README.md](https://github.com/OpenTechIL/agentry/blob/main/packaging/README.md#signing--cosign-keyless-sigstore).

## A catalog fetch hangs or fails

Catalog fetches time out (15s) and cap the response size. On failure agentry reports
`catalog '<name>': fetch failed: …` and falls back to nothing — it does not silently
continue with a stale cache.

```bash
agentry catalog list                  # what's configured
agentry catalog remove <name>         # drop an unreachable one
```

The default catalog is a remote URL registered by `agentry init`. Skip it with
`agentry init --no-default-catalog` if you work offline.

## Still stuck?

Open an issue with the output of `agentry --version` and `agentry doctor`:
<https://github.com/OpenTechIL/agentry/issues>
