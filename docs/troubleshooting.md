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

**Hook events are shared, not refused.** A hook event such as `PreToolUse` holds a list of hook
groups. If you already have that event, agentry appends its groups after yours, keeps your
groups and their order, and removes only its own groups on uninstall. So the "already has …"
refusal below applies to named entries such as MCP servers. For hooks, it applies only when the
event exists but isn't a list:

```text
demo/hook/c: .claude/settings.json already has 'hooks.Stop' (not added by agentry) as an object, not a list — left unchanged; demo/hook/c was not merged into claude. Fix or remove your entry, or disable demo/hook/c, then re-run sync.
```

Fix that event so it holds a list (or remove it), then re-run sync. Hook-specific warnings are
under [hook groups](#hook-groups-edited-or-removed).
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

### Hook groups edited or removed

```text
demo/hook/a: 1 hook group of 'hooks.PreToolUse' in .claude/settings.json no longer matches what agentry installed (edited or removed) — left as is. To reinstall, disable demo/hook/a, sync, then re-enable it; or run `agentry keep demo/hook/a PreToolUse` to keep yours.
```

With several, it reads `2 hook groups of … no longer match …`.

**Cause.** You edited or deleted a hook group that agentry added to an event. agentry can't tell
an edit from a deletion here. Re-adding the original could make the same hook run twice, so it
leaves the event as it is. `status` shows the row as `drift`. The component's other events, and
other packs' groups on the same event, keep updating.

**Fix.** Choose one:
- **Keep your version.** Run `agentry keep <ref> <event>` (the message gives the exact
  command). This hands that component's groups on that event over to you. agentry stops writing
  to that event for that component, and other packs are unaffected.
- **Reinstall agentry's version.** Run `agentry disable <ref>`, `agentry sync`, then
  `agentry enable <ref>`.

### `your copy of <ref>'s '<event>' hook group … differs from upstream`

```text
demo/hook/a: your copy of demo/hook/a's 'hooks.PreToolUse' hook group in .claude/settings.json differs from upstream — keeping yours; delete it to take demo/hook/a's version, or run `agentry keep demo/hook/a PreToolUse` to keep yours (this stops demo/hook/a adding hooks to PreToolUse).
```

**Cause.** You already had a hook group identical to one a pack ships, so agentry used yours
instead of adding a duplicate. The pack's version has since changed upstream. Your group is
still yours, so agentry leaves it alone. It also doesn't add the new version next to it, because
the hook would then run twice. `status` shows the row as `drift`. With several groups, it reads
`your copies of 2 of <ref>'s '<event>' hook groups … differ from upstream`.

**Fix.** Choose one:
- **Take the pack's version.** Delete your group and run `agentry sync`. agentry installs the
  new version and manages it from then on.
- **Keep yours.** Run the `agentry keep …` command from the message. This also stops that pack
  adding any hooks to that event.

### `'<pointer>' in <file> is a <type>, not an object`

```text
<ref>: '<pointer>' in <file> is a list, not an object — not merged. Fix the file by hand, then re-run sync.
```

**Cause.** The section agentry merges into (for example `mcpServers` or `servers`) exists but
holds a list, string, number, boolean or `null` instead of an object. Every component that
targets that section is skipped, and agentry never replaces the section.

**Fix.** Edit the file so that the section is an object, for example `"mcpServers": {}`. Keep
anything you need from the old value. Then run `agentry sync`.

### `is already provided by <other>, listed earlier in .agentry.yml`

```text
two/mcp/gh: 'mcpServers.github' in .mcp.json is already provided by one/mcp/gh, listed earlier in .agentry.yml — skipped. Disable one of them, rename the entry, or reorder components to choose which wins.
```

**Cause.** Two enabled components ship an entry with the same name for the same file, and only
one can have it. The one listed first under `components:` in `.agentry.yml` wins, on every
machine, whatever order they were installed in. The other is skipped for that entry. On first
install the whole component is skipped for that file, and once installed, its other entries
keep updating. `status` shows it as `missing` with the reason, and `doctor --strict`
fails.

**Fix.** Choose one:
- **Pick the winner by order.** Move the component you want above the other in
  `.agentry.yml` and run `agentry sync`. agentry swaps the entry over in that sync (see
  [`now comes from`](#now-comes-from-instead-of)).
- **Drop one.** Run `agentry disable` on the component you don't want.
- **Keep both.** Rename the entry in one of the sources, or ask its author to.

If the losing component is a dependency that another component pulls in through `requires`,
it isn't listed in `.agentry.yml`. Components you list always come before dependencies, so the
listed one wins, and the message tells you how to change that:

```text
one/mcp/gh: 'mcpServers.github' in .mcp.json is already provided by two/mcp/gh — skipped. Declare one/mcp/gh in .agentry.yml above two/mcp/gh, rename the entry, or disable two/mcp/gh.
```

To let the dependency win, add it to `components:` above the other one. If both colliding
components are dependencies, the message ends with `declare <ref> in .agentry.yml to give it
priority` instead.

### `is already installed by <other>, listed earlier in .agentry.yml` (a file)

```text
two/command/review: .claude/commands/review.md is already installed by one/command/review, listed earlier in .agentry.yml — skipped on claude. Disable one of them, or reorder components to choose which wins.
```

**Cause.** Two components install a file at the same path, for example two sources that both
ship `commands/review.md`. As with entries, the one listed first in `.agentry.yml` gets the
path, and the other is skipped on that target. Earlier versions let the later one silently
overwrite the earlier.

**Fix.** Reorder the components to choose which file you get, or disable the one you don't
want. When the loser is a dependency that isn't listed in `.agentry.yml`, the message names the
listed winner:

```text
one/command/review: .claude/commands/review.md is already installed by two/command/review — skipped on claude. Declare one/command/review in .agentry.yml above two/command/review, or disable two/command/review.
```

Add the dependency to `components:` above the other to let it win. If both colliding
components are dependencies (two packages pulled in by `requires`, say), the one required first
wins, and neither can be disabled directly:

```text
depb/skill/rev: .claude/skills/rev is already installed by depa/skill/rev — skipped on claude. To change that, declare depb/skill/rev in .agentry.yml to give it priority.
```

### `now comes from … instead of …`

```text
~ .claude/commands/review.md now comes from two/command/review (listed earlier in .agentry.yml) instead of one/command/review — to keep one/command/review's version, list it before two/command/review in .agentry.yml, or disable two/command/review
```

**Cause.** This isn't an error. agentry printed it once, in the sync where the owner of a
contested file or entry changed. Usually that's because you reordered `components:` or added a
component above an existing one. After an upgrade, it can also be a file that an earlier version
let the later-listed component overwrite. agentry moved the file or entry to the component
listed first. It removes the old owner's entry only if you hadn't edited it.

When the previous owner was a dependency, the line tells you to declare it instead:

```text
~ .mcp.json:mcpServers.github now comes from two/mcp/gh instead of one/mcp/gh — to keep one/mcp/gh's version, declare one/mcp/gh in .agentry.yml above two/mcp/gh, or disable two/mcp/gh
```

If both are dependencies, it reads:

```text
~ .claude/skills/rev now comes from depa/skill/rev instead of depb/skill/rev — to keep depb/skill/rev's version, declare depb/skill/rev in .agentry.yml to give it priority
```

**Fix.** Nothing, if that's the version you want. Otherwise do what the line says: list or
declare the other component first, or disable the winner.

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

## A Claude plugin source reports `plugin.json` warnings

When a source is a Claude Code plugin, agentry reads its `.claude-plugin/plugin.json` (see
[authoring](authoring.md#claude-code-plugins-claude-pluginpluginjson)) and tells you about
anything it couldn't follow. Each warning names the source. `sync` still exits 0, and
`agentry doctor` lists them under the `plugin` category, so `doctor --strict` fails. They come
from the plugin's files, so the lasting fix is usually the plugin author's.

### A key agentry has no equivalent for

```text
source 'plug': plugin.json `outputStyles` and `lspServers` have no agentry equivalent — skipped. They still work when the plugin is installed in Claude Code itself.
```

agentry has no component type for `outputStyles`, `workflows`, `themes`, `lspServers`,
`monitors`, `userConfig`, `channels` or `bin`. It prints one line per source, listing that
source's keys. Everything else in the plugin still installs. Install the plugin in Claude Code
itself if you need those parts. When two or more sources have such keys, sync folds them into
one line, and `-v` shows each source:

```text
2 plugin sources use features agentry doesn't install — run with --verbose for the list
```

### A path that doesn't exist, or leaves the plugin

```text
source 'plug': plugin.json 'commands' path './missing' does not exist — skipped it. Fix the path in plugin.json.
source 'plug': plugin.json 'mcpServers' path '../outside.json' leaves the plugin (..) — skipped it. Use a relative path inside the plugin, e.g. './.mcp.json'.
```

agentry only reads files inside the source. Fix the path in plugin.json (or ask the author to).
Paths that do resolve are still used.

### A `hooks` or `mcpServers` value agentry can't use

```text
source 'p': plugin.json 'mcpServers' value 42 is neither a path nor an object — skipped it. Use a relative path inside the plugin (e.g. './.mcp.json') or an inline object.
source 'p': plugin.json 'hooks' value 7 is neither a path nor an object — skipped it. Use a relative path inside the plugin (e.g. './hooks/hooks.json') or an inline object.
```

`hooks` and `mcpServers` can each be a path, an inline object, or a list of those. A number, or a
list item that's neither a path nor an object, can't be used, so agentry skips just that value
and merges nothing from it. The rest of the list still counts. A list of objects merges all of
its items into one component (`plugin-hooks` or `plugin-mcp`). For example, two inline
`mcpServers` objects installed both servers. Fix the value in plugin.json, or ask the plugin
author to.

For `commands`, `agents` and `skills`, which take only paths, the message ends at the path
example (for instance `… 'agents' value "" is neither a path nor an object — skipped it. Use a
relative path inside the plugin (e.g. './agents').`).

### Inline commands or agents

```text
source 'plug': plugin.json agents 'inline-agent' is inline content, which agentry can't install as a file — skipped it. Ship it as the file agents/inline-agent.md instead.
```

agentry installs commands and agents as files. Move the inline content into a file in the
plugin, or ask the author to.

### An MCP bundle

```text
source 'plug': plugin.json mcpServers './server.mcpb' is an MCP bundle, which agentry can't install — skipped it. Ask the plugin author for a JSON mcpServers entry.
```

`.mcpb` and `.dxt` bundles aren't supported. A plain JSON `mcpServers` entry installs normally.

### The same component from a file and from plugin.json

```text
source 'clash': plugin.json would add skill 'a', but skills/a already provides it — installed the file one; the plugin.json one is not installed. Rename the file to install both.
```

Both define a skill named `a`. agentry installs the file-based one. Rename one of them in the
plugin to get both.

### An invalid plugin.json

```text
source 'bad': .claude-plugin/plugin.json is not valid (Expecting property name enclosed in double quotes: line 2 column 1 (char 17)) — ignored it and used the convention layout (skills/, commands/, agents/, hooks/, .mcp.json). Fix the file, or ask the plugin author to.
```

### An inline plugin hook that runs `${CLAUDE_PLUGIN_ROOT}/…`

```text
p/hook/plugin-hooks: hook event 'Stop' in .claude-plugin/plugin.json runs '${CLAUDE_PLUGIN_ROOT}/run.sh', and agentry doesn't support ${CLAUDE_PLUGIN_ROOT} paths in inline plugin hooks yet — skipped it on claude. Install the plugin in Claude Code itself to use this hook.
```

The plugin defines its hooks inline in plugin.json, and one of them calls a script by its
`${CLAUDE_PLUGIN_ROOT}` path. agentry can't rewrite that path for inline hooks yet, so it skips
that event and installs the plugin's other hook events normally (here `PreToolUse` merged and
`Stop` was skipped). `agentry why <ref>` shows where the component is defined:

```text
p/hook/plugin-hooks
├── source: p git file:///…/p @ 21b10170e89b (ref main)
├── defined in: .claude-plugin/plugin.json (hooks)
└── installs to
    └── claude  .claude/settings.json:hooks  ok
```

To use the skipped hook, install the plugin in Claude Code itself.

### `${CLAUDE_PLUGIN_DATA}` in a hook or MCP entry

```text
plug/hook/hooks: references ${CLAUDE_PLUGIN_DATA}, which only exists inside an installed Claude plugin — installed as-is, but it won't resolve. Set CLAUDE_PLUGIN_DATA in your environment, or ask the plugin author for a fallback.
```

Claude Code sets `CLAUDE_PLUGIN_DATA` only for plugins it installed itself. Installed through
agentry, the entry still lands, but the variable is empty unless you set it.

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

**Kept keys with no entry on a target** are summarized too, as
`2 keys in keep: have no entry on one or more targets and were not installed — …`. See
[`kept, but no entry on <target>`](#kept-but-no-entry-on-target).

**Plugin sources with features agentry doesn't install** are summarized as
`2 plugin sources use features agentry doesn't install — run with --verbose for the list`. See
[plugin.json warnings](#a-claude-plugin-source-reports-pluginjson-warnings).

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
on warnings too — which makes it a good CI gate. Errors are listed first below, then warnings.

| Category | Level | Meaning |
|---|---|---|
| `target` | error | A target in `targets:` has no built-in driver, no `target_profiles` entry, and no installed overlay. See [not defined](#target-name-is-not-defined-nothing-was-changed). |
| `resolve` | error | A source or dependency couldn't be resolved: git failed or timed out, a ref doesn't exist, a local source path is missing, or a dependency conflicts. The remaining checks are skipped until this is fixed. |
| `component` | error | A component references a source that isn't declared (`unknown source`), or the source doesn't actually provide it (`not provided by source`). Run `agentry list` for the real names. |
| `target` | warn | A component's `targets:` names a target the project doesn't list, so it's skipped there. See [not in the project's targets](#target-name-is-not-in-the-projects-targets-skipped). |
| `support` | warn | No active target installs this component type, so it goes nowhere. |
| `format` | warn | A component's content visibly won't work in that target. It's installed as-is. See [format warnings](#a-component-may-not-work-on-its-target-format-warnings). |
| `plugin` | warn | A Claude plugin source's `plugin.json` has something agentry couldn't install or follow, or a plugin entry references `${CLAUDE_PLUGIN_DATA}`. See [plugin.json warnings](#a-claude-plugin-source-reports-pluginjson-warnings). |
| `env` | warn | A merge fragment references `${VAR}`, which is unset. agentry ships the placeholder; your agent resolves it at runtime. |
| `drift` | warn | On-disk state doesn't match config/lock. The line says why and what to do: usually `agentry sync`, but for an entry you edited by hand or a merge agentry refused, follow the fix it names. |
| `command` | warn | The `agy` alias on `PATH` belongs to another tool (see above). |

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

To install a catalog overlay:

```bash
agentry target list          # what overlays are available
agentry target add foo       # shows the destinations, then asks before applying
```

`target add` prints every destination the overlay will write to and prompts for
confirmation, because an overlay arrives over the network and dictates where files land.
Pass `--yes` in CI once you've reviewed it.

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

## `agentry update <source>` fails: `now requires … but the pinned source … doesn't provide`

**Symptom.**

```text
a/skill/ra now requires b/skill/newthing, but the pinned source 'b' doesn't provide 'newthing'. Nothing was changed. Update both: `agentry update a b`
```

**Cause.** `agentry update a` updates only source `a`. Every other source keeps the revision
pinned in `.agentry.lock`. The new revision of `a` depends on a component that the pinned
revision of `b` doesn't have yet. Rather than install half of it, agentry stops before writing
anything.

**Fix.** Update both sources together, as the message says (`agentry update a b`), or run a bare
`agentry update` to update everything.

A misspelled name fails the same way, without changes:

```text
unknown source 'aa' — did you mean 'a'? Nothing was changed. See `agentry source list`.
```

## `--frozen` fails in CI

`sync --frozen` installs strictly from `.agentry.lock` and refuses to resolve anything new.
It fails when:

- **a source isn't in the lock** — someone edited `.agentry.yml` without running `agentry sync`
  locally. Run it and commit the updated lock.
- **the lock is out of date relative to config** — same fix.
- **the store is missing** — `.agentry/` is git-ignored, so a fresh CI checkout has to clone.
  That's expected and `--frozen` handles it. If it fails here, the pinned commit is gone from
  the remote; see [locked commit is no longer on origin](#locked-commit-is-no-longer-on-origin).

Once a source is cloned and its pinned commit is in `.agentry/`, `sync`, `sync --frozen`,
`status` and `doctor` don't contact the remote at all, so they also work offline.

## `locked commit … is no longer on origin`

**Symptom.**

```text
source 'a': locked commit 56462872c9f8 is no longer on origin (force-pushed or deleted?). Run `agentry update a` to re-pin.
```

**Cause.** `.agentry.lock` pins the source to a commit that your local `.agentry/` store
doesn't have, and the remote doesn't have it any more either. Someone force-pushed over it, or
deleted the branch it was on. This usually shows up on a fresh checkout, such as CI, where
the store has to be cloned from scratch. agentry stops instead of installing a different
commit.

**Fix.** Re-pin the source to what the remote has now, then commit the updated lock:

```bash
agentry update a
```

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
