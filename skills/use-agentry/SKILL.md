---
name: use-agentry
description: Use when the user asks to add or install an AI skill — "add skill <github url or owner/repo>", "install skill …" — or when they paste a skill-manager command like `npx skills add …`. Routes skill installation through agentry (`agy`) so the skill is tracked in `.agentry.yml`/`.agentry.lock` instead of installed opaquely.
---

# Use agentry (`agy`) to install skills

This project manages AI-agent skills with **agentry** — CLI `agy`, a dependency manager that
records every skill in `.agentry.yml` + `.agentry.lock` and installs it into each AI tool's
native layout (`.claude/skills/…`, `.agents/skills/…`, etc.). Prefer `agy` over ad-hoc
installers (`npx skills add`, curl-to-shell, manual clones) so installs stay reproducible and
reversible.

## When this skill applies

Trigger on either:

- **Natural-language request** — "add skill `<X>`", "install skill `<X>`", "get the skill at
  `<X>`", where `<X>` is a GitHub URL or `owner/repo` shorthand.
- **A pasted skill-manager command** — e.g. `npx skills add owner/repo`,
  `npx @something/skills install …`, or any other tool that installs an agent skill.

## Behavior

**Natural-language request → default to agentry.** Do not ask which tool to use. Translate the
request into `agy` commands and run them (steps below).

**Pasted concrete command → offer the choice.** The user typed a specific command, so present
two options and let them pick:

1. **Use agentry (recommended)** — run the equivalent `agy` commands so the skill is tracked.
2. **Run it as-is** — execute exactly what they pasted, unchanged.

Only run the agentry path if they choose it; otherwise run their command verbatim.

## Preflight

Confirm `agy` is installed:

```bash
agy version
```

If it is missing, do **not** silently fall back to `npx`. Tell the user to install agentry
(the repo ships `install.sh` / `install.ps1`, and it's on Homebrew/Scoop) and stop.

## Translating a request into `agy` commands

Let `<X>` be a full GitHub URL or `owner/repo` shorthand.

1. **Normalize.** `owner/repo` → `https://github.com/owner/repo`. Derive a short **source
   name** from the last path segment (e.g. `OpenTechIL/markitdown-for-ai` → `markitdown-for-ai`).

2. **If a configured catalog already lists it by name**, that's the one-liner:

   ```bash
   agy add <name>                 # whole repo
   agy add <name> --type skill    # only its skills
   agy add <name>@one,two         # only the named components
   ```

3. **Otherwise register the repo as a source and add its skill, then sync.** Inspect what the
   repo provides before naming a component — never invent a component name:

   ```bash
   agy source add <name> https://github.com/<owner>/<repo>
   agy list                          # see the real skill/component names it provides
   agy add <name>/skill/<skill>      # conventional layout: skills/<skill>/
   agy sync
   ```

   When the repo **root itself is the skill** (a `SKILL.md` at the repo root, no `skills/`
   dir), bypass discovery with `--path .`:

   ```bash
   agy source add <name> https://github.com/<owner>/<repo>
   agy add <name>/skill/<name> --path .
   agy sync
   ```

4. **Self-installing skills** (no skill file — they generate one via their own CLI, e.g. a
   `uv`/`npx` installer): declare the commands and the paths they produce; running them is
   opt-in with `--allow-run`:

   ```bash
   agy add <name>/skill/<name> \
     --generate-setup "uv tool install <pkg>" \
     --generate-command "<pkg> install --project" \
     --produces ".claude/skills/<name>"
   agy sync --allow-run
   ```

## After installing

- Run `agy sync` if you haven't already.
- Confirm where it landed: `agy why <name>/skill/<skill>` or `agy status`.
- Report the installed path(s) to the user.

## Guardrails

- Never guess a component name — use `agy list` to read the real names a source provides.
- Surface `agy`'s own errors to the user rather than working around them.
- Don't mix installers: if a skill is managed by `agy`, remove it with `agy remove …`, not by
  deleting files.
