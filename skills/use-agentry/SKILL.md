---
name: use-agentry
description: Use when the user asks to add or install an AI skill — "add skill <github url or owner/repo>", "install skill …" — or when they paste a skill-manager command like `npx skills add …`. Also use when they want a repo listed in agentry's starter catalog/registry ("add <repo> to the agentry registry", "publish this skill so others can `agentry add` it by name") — it drives `gh` to file the issue or open the catalog PR. Routes skill installation through agentry so the skill is tracked in `.agentry.yml`/`.agentry.lock` instead of installed opaquely.
---

# Use agentry to install skills

This project manages AI-agent skills with **agentry** — a dependency manager that
records every skill in `.agentry.yml` + `.agentry.lock` and installs it into each AI tool's
native layout (`.claude/skills/…`, `.agents/skills/…`, etc.). Prefer `agentry` over ad-hoc
installers (`npx skills add`, curl-to-shell, manual clones) so installs stay reproducible and
reversible.

## When this skill applies

Trigger on any of:

- **Natural-language request** — "add skill `<X>`", "install skill `<X>`", "get the skill at
  `<X>`", where `<X>` is a GitHub URL or `owner/repo` shorthand.
- **A pasted skill-manager command** — e.g. `npx skills add owner/repo`,
  `npx @something/skills install …`, or any other tool that installs an agent skill.
- **A catalog-contribution request** — "add `<X>` to the agentry registry/catalog", "get this
  skill listed so `agentry add <name>` works", "open a PR/issue to register `<X>`". That path is
  [Contributing a repo to the starter catalog](#contributing-a-repo-to-the-starter-catalog),
  not an install.

## Behavior

**Natural-language request → default to agentry.** Do not ask which tool to use. Translate the
request into `agentry` commands and run them (steps below).

**Pasted concrete command → offer the choice.** The user typed a specific command, so present
two options and let them pick:

1. **Use agentry (recommended)** — run the equivalent `agentry` commands so the skill is tracked.
2. **Run it as-is** — execute exactly what they pasted, unchanged.

Only run the agentry path if they choose it; otherwise run their command verbatim.

## Preflight

Confirm agentry is installed:

```bash
agentry version
```

If it is missing, do **not** silently fall back to `npx`. Tell the user to install agentry
(the repo ships `install.sh` / `install.ps1`, and it's on Homebrew/Scoop) and stop.

## Translating a request into `agentry` commands

Let `<X>` be a full GitHub URL or `owner/repo` shorthand.

1. **Normalize.** `owner/repo` → `https://github.com/owner/repo`. Derive a short **source
   name** from the last path segment (e.g. `OpenTechIL/markitdown-for-ai` → `markitdown-for-ai`).

2. **If a configured catalog already lists it by name**, that's the one-liner:

   ```bash
   agentry add <name>                 # whole repo
   agentry add <name> --type skill    # only its skills
   agentry add <name>@one,two         # only the named components
   ```

3. **Otherwise register the repo as a source and add its skill, then sync.** Inspect what the
   repo provides before naming a component — never invent a component name:

   ```bash
   agentry source add <name> https://github.com/<owner>/<repo>
   agentry list                          # see the real skill/component names it provides
   agentry add <name>/skill/<skill>      # conventional layout: skills/<skill>/
   agentry sync
   ```

   When the repo **root itself is the skill** (a `SKILL.md` at the repo root, no `skills/`
   dir), bypass discovery with `--path .`:

   ```bash
   agentry source add <name> https://github.com/<owner>/<repo>
   agentry add <name>/skill/<name> --path .
   agentry sync
   ```

4. **Self-installing skills** (no skill file — they generate one via their own CLI, e.g. a
   `uv`/`npx` installer): declare the commands and the paths they produce; running them is
   opt-in with `--allow-run`:

   ```bash
   agentry add <name>/skill/<name> \
     --generate-setup "uv tool install <pkg>" \
     --generate-command "<pkg> install --project" \
     --produces ".claude/skills/<name>"
   agentry sync --allow-run
   ```

## After installing

- Run `agentry sync` if you haven't already.
- Confirm where it landed: `agentry why <name>/skill/<skill>` or `agentry status`.
- Report the installed path(s) to the user.

## Contributing a repo to the starter catalog

Installing is local; **contributing** gets a repo listed in agentry's starter catalog
(`registry/repositories.json` in `OpenTechIL/agentry`) so anyone can `agentry add <name>` by name.
Two routes — file an issue for a maintainer, or open the PR yourself.

### Preflight

```bash
gh auth status                                   # must be authenticated
gh repo view <owner>/<repo> --json visibility,isArchived,defaultBranchRef
```

The catalog only lists repos that are **public and anonymously clonable** — if the target is
private or archived, say so and stop. If `gh` is missing or unauthenticated, tell the user to
install it / run `gh auth login`; do not fall back to the web UI on their behalf.

Check for a duplicate before writing anything:

```bash
gh issue list --repo OpenTechIL/agentry --state all --search "<name> in:title"
gh pr list   --repo OpenTechIL/agentry --state all --search "<name> in:title"
```

Then ask the user which route they want (issue = fastest, a maintainer authors the entry;
PR = you author it and they review).

### Route A — request it via an issue

Blank issues are disabled in the web UI, but `gh` posts through the API, so a plain body is
fine. Mirror the feature-request template's shape so a maintainer can act without a round trip:

```bash
gh issue create --repo OpenTechIL/agentry \
  --title "catalog: add <name> (<owner>/<repo>)" \
  --label enhancement \
  --body "$(cat <<'EOF'
### Repo

https://github.com/<owner>/<repo> (ref: `main`, subdir: `<none>`)

### Catalog name

`<name>`

### Summary

<one line, ≤100 chars, as it should appear in `summary`>

### Components it provides

<output of `agentry list` for the source, or the repo's skills/ layout — real names only>

### Notes

<generate-style installer? hooks needing target_profiles? namespacing? otherwise "none">
EOF
)"
```

Report the issue URL back to the user.

### Route B — open the pull request

1. **Work in a clone of `agentry`, never in the user's project.** If the cwd already *is* the
   agentry repo, just branch. Otherwise fork+clone to a scratch dir:

   ```bash
   cd <scratch-dir>
   gh repo fork OpenTechIL/agentry --clone   # creates the fork if needed, then clones it here
   cd agentry
   git switch -c catalog/add-<name>
   ```

2. **Author the entry with `agentry`, not by hand** — it writes the schema correctly and defaults
   to `registry/repositories.json` (run it from the clone root):

   ```bash
   agentry catalog add-repo https://github.com/<owner>/<repo> <name> \
     --summary "<one line>" \
     --discover                      # clone the repo and pre-fill `expose` with real names
   ```

   A `https://github.com/<owner>/<repo>/tree/<ref>/<subdir>` URL infers `ref` and `subdir`;
   otherwise pass `--ref` / `--subdir` explicitly. Omit `--discover` only when the whole repo
   should be exposed. Add `--force` solely to update an entry the user already owns.

3. **Verify before committing:**

   ```bash
   git diff registry/repositories.json
   python3 -c "import json;json.load(open('registry/repositories.json'))"
   uv run --extra dev pytest tests/test_registry.py -q
   ```

4. **Commit and open the PR** (imperative subject, per this repo's convention):

   ```bash
   git commit -am "add <name> to starter catalog"
   git push -u origin catalog/add-<name>
   gh pr create --repo OpenTechIL/agentry --base main \
     --head <fork-owner>:catalog/add-<name> \
     --title "add <name> to starter catalog" \
     --body "$(cat <<'EOF'
## What & why

Adds `<name>` (https://github.com/<owner>/<repo>) to the starter catalog so it installs by
name with `agentry add <name>`. <One line on what the repo provides.>

## Checklist

- [x] Catalog-only change — entry generated with `agentry catalog add-repo --discover`
- [x] `registry/repositories.json` parses and `uv run --extra dev pytest tests/test_registry.py` passes
- [x] Repo is public, anonymously clonable, and pinned to ref `<ref>`
- [x] No code/behavior change, so no new tests or docs updates

## Notes for reviewers

<components exposed, and anything unusual: generate installers, hooks, namespacing>
EOF
)"
```

   Drop `--head` when you're in a direct clone with push access. Then report the PR URL. If the
   push fails for lack of permission, the fork step was skipped — fork first rather than retrying
   against `OpenTechIL/agentry`.

## Guardrails

- Never guess a component name — use `agentry list` to read the real names a source provides.
- Catalog contributions go through a branch on a fork (or a topic branch) and a PR — never
  commit to `main` or push directly to `OpenTechIL/agentry`.
- Don't edit `registry/repositories.json` inside the user's own project (their `.agentry/`
  store or a vendored copy); the catalog only counts in the `agentry` repo.
- Show the user the issue/PR body before it goes out, and don't invent a summary for a repo
  you haven't looked at.
- Surface `agentry`'s own errors to the user rather than working around them.
- Don't mix installers: if a skill is managed by `agentry`, remove it with `agentry remove …`, not by
  deleting files.
