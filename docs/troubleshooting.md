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
leaves the file alone, skips that component **for that file only**, and prints one of the
warnings below. `sync` still exits 0.

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
overwrite something it didn't create.

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
<ref>: '<pointer>.<key>' in <file> is your entry (it matched <ref> earlier, but upstream has changed) — keeping yours. Delete it and re-run sync to take <ref>'s version.
```

**Cause.** This follows the previous case. The source has since updated the entry, and yours no
longer matches it. agentry still won't change your entry.

**Fix.** To take the new version, delete your entry and run `agentry sync`. To keep yours, run
`agentry disable <ref>`.

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
of them can have it.

**Fix.** Run `agentry disable` on whichever component you don't want. If you need both, rename
the entry in one of the sources, or ask its author to.

### `was edited after agentry installed it` (status shows `drift`)

```text
<ref>: '<pointer>.<key>' in <file> was edited after agentry installed it — keeping your version. Delete it and re-run sync to restore agentry's.
```

**Cause.** You changed an entry that agentry installed. agentry keeps your edit and stops
updating that entry from upstream. The warning repeats on every sync until you resolve it.
`agentry doctor` lists every such entry with its file and component (see the
[example below](#was-recorded-by-an-older-agentry-and-differs-from-the-current-source)).

**Fix.** To keep your edit, leave it. agentry won't delete it on removal either (see the next
entry). To go back to agentry's version and receive upstream updates again, delete the entry and
run `agentry sync`.

### `was recorded by an older agentry and differs from the current source`

```text
demo/mcp/github: 'mcpServers.github' in .mcp.json was recorded by an older agentry and differs from the current source — keeping it. If you didn't edit it, delete it and re-run sync to get the current version.
```

`agentry status` shows the row as `drift`. To get the full list of affected entries, run
**`agentry doctor`**. It prints one line per entry, giving the file, the `pointer.key`, the
component that installed it, and the fix. It also lists entries you edited by hand in the same
format, and `--strict` fails on any of them:

```text
! drift  .mcp.json: 'mcpServers.github' (from demo/mcp/github → claude) was recorded by an older agentry and differs from the current source — delete it and run `agentry sync` to get the current version, or disable demo/mcp/github to keep yours
! drift  .mcp.json: 'mcpServers.slack' (from demo/mcp/slack → claude) was edited by hand — delete it and run `agentry sync` to get the current version, or disable demo/mcp/slack to keep yours
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

**Fix.** Go through `agentry doctor`'s list. For each entry you edited and want to keep, leave
it, or run `agentry disable <ref>` to stop the warning. Delete every other entry and run
`agentry sync`. agentry then installs the current version and manages it from then on.

### `was edited after install — left in place on removal`

```text
<ref>: '<pointer>.<key>' in <file> was edited after install — left in place on removal; delete it by hand if unused.
```

**Cause.** You removed or disabled a component whose entry you had edited. agentry treats the
edited entry as yours, so it doesn't delete it.

**Fix.** If you no longer need the entry, delete it from the file by hand.

If you remove a component whose entry agentry couldn't verify after an upgrade (see
[the previous entry](#was-recorded-by-an-older-agentry-and-differs-from-the-current-source)),
you get a variant of this warning:

```text
<ref>: '<pointer>.<key>' in <file> could not be verified as agentry's (recorded by an older agentry) — left in place on removal; delete it by hand if unused.
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

## Reading `agentry doctor`

`doctor` is a read-only preflight. It exits non-zero on **errors** and, with `--strict`,
on warnings too — which makes it a good CI gate.

| Category | Level | Meaning |
|---|---|---|
| `target` | error | A target in `targets:` has no built-in driver, no `target_profiles` entry, and no installed overlay. |
| `resolve` | error | A source or dependency couldn't be resolved: git failed or timed out, a ref doesn't exist, a local source path is missing, or a dependency conflicts. The remaining checks are skipped until this is fixed. |
| `component` | error | A component references a source that isn't declared (`unknown source`), or the source doesn't actually provide it (`not provided by source`). Run `agentry list` for the real names. |
| `support` | warn | No active target installs this component type, so it goes nowhere. |
| `env` | warn | A merge fragment references `${VAR}`, which is unset. agentry ships the placeholder; your agent resolves it at runtime. |
| `drift` | warn | On-disk state doesn't match config/lock. Run `agentry sync`. |
| `command` | warn | The `agy` alias on `PATH` belongs to another tool (see above). |

## An undefined target during `sync`

**Symptom.**

```text
target 'foo' is undefined — define it under target_profiles in .agentry.yml, or install a shared driver overlay (`agentry target list`)
```

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
