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
