# Design: the `transform` seam — semantic content translation (incl. AGENTS.md emit)

*Design spec — 2026-06-30. Status: **for review, not yet approved for implementation**.*

---

## Context

agentry installs components by **placement**: a symlink (link/copy) or a key-scoped config
merge. It never rewrites a component's *content*. That is deliberate — it's what makes the
killer features possible: live-update symlinks (edit once, every harness sees it) and
reversible, never-clobber installs.

But three real cross-tool needs require touching content, not just placement:

1. **AGENTS.md emit / composition** — produce a single `AGENTS.md` (the cross-tool standard)
   from a project's components, so an agent that only reads `AGENTS.md` still benefits.
2. **Per-target frontmatter / format translation** — e.g. a Claude agent `.md` whose
   frontmatter a different harness rejects (apm's evidenced #581 — a deployed agent file
   crashes OpenCode). Today agentry would *place* the file unchanged and share that risk.
3. **Deep field/format renames** beyond what the merge layer already does (the merge
   `pointer`/`aliases` already handle `mcpServers`↔`servers`↔`mcp_servers`; this is about
   richer, schema-level rewrites).

The `Driver` dataclass already **reserves** a hook for this:
`transform: TransformFn | None` where `TransformFn = Callable[[ComponentType, dict], dict]`
(`drivers/base.py`). It is `None` on every driver today — placement-mapping only.

**This spec exists because implementing `transform` is not a data edit — it's an architectural
choice with genuine tensions, and the chosen direction (per product steer) is to make an
*AI-agent–driven* semantic merge the default for these cases.** We want that decision recorded
and reviewed before code.

---

## The core tension

**You cannot symlink a file *and* rewrite its content.** Transformation forces a
**copy-with-rewrite** install path. So any transformed component:

- **loses live-update** (the output is a generated copy, not a live symlink into the store);
- is **non-deterministic** if produced by an AI agent — which collides head-on with agentry's
  reproducibility guarantees (byte-stable lockfile, idempotent `sync`, `--frozen`);
- introduces **content from an LLM** into a tool that today proudly *runs nothing* and embeds
  no model credentials (the "nothing of it runs while your agents do" promise).

A design that ignores any of these breaks something users currently rely on. The recommendation
below is shaped entirely by *containing* these three tensions.

---

## Constraints the design must honor

1. **Don't regress live-update for untransformed components.** Transform is strictly opt-in;
   the default install path is unchanged.
2. **Stay reproducible.** A transformed artifact must not be silently regenerated on every
   `sync` (an AI would produce different bytes each time, and `--frozen`/CI would thrash).
3. **Keep agentry credential-free and runtime-light.** agentry should not embed an LLM API key
   or a model SDK. If an AI performs the merge, it runs through the **user's already-installed
   agent CLI** (claude / codex / llm / …) — the same posture as apm's `apm run`, and the
   `generate` strategy's "run the user's own tool" precedent.
4. **Safe and consented.** Invoking an agent to rewrite content is code-execution-adjacent and
   sends content to a model. It must be **gated and confirmed**, never silent.
5. **Reversible.** A transformed artifact is manifest-tracked and removed cleanly, like `copy`.

---

## Approaches considered

| Approach | What | Pros | Cons |
|---|---|---|---|
| **A. Deterministic transform** | Code/rule-based rewrites (frontmatter strip, field rename, template-based AGENTS.md concatenation) | Pure, fast, reproducible, no LLM | Only handles known mappings; can't do genuine semantic merge; brittle across formats |
| **B. AI-agent transform** | Shell out to the user's agent CLI with a prompt to compose/translate the content | Handles the open-ended semantic cases (compose AGENTS.md, reconcile differing frontmatter); matches the "write-once-run-anywhere" ambition | Non-deterministic; needs confirmation + a runtime; trust surface |
| **C. Hybrid (recommended)** | Deterministic where a rule exists; **AI-agent as the default for semantic cases**; placement-only fallback when neither applies or the user opts out | Best coverage; degrades gracefully; honors the product steer | Most moving parts; must clearly signal which path ran |

**Recommendation: C (hybrid), with the AI-agent provider as the default for the semantic cases,
per the product steer — but always confirmed and always optional.**

---

## Recommended design

### 1. A new install path: `transform` (copy-with-rewrite)

Add a fifth install outcome alongside link / copy / merge / link+merge / generate: a component
marked for transform is **materialized as a committed file** (like `copy`), but its bytes are
produced by a transform provider rather than copied verbatim. It is:

- written to the target's destination as a **real file** (git-diffable, reviewable),
- recorded in the manifest (reversible removal, drift detection),
- **not regenerated on a plain `sync`** — only on explicit `agy transform` / `agy sync --transform`
  (and never under `--frozen`). The lockfile/manifest stores the **output content hash**, so a
  plain `sync` verifies the committed artifact matches and never re-invokes the agent.

This single rule resolves the reproducibility tension: **AI runs at author time, output is
committed and reviewed, CI just verifies the hash.** It mirrors how the report frames apm's
compile artifact — except here it's opt-in and per-component, not the whole model.

### 2. Transform providers

A provider implements `transform(component_type, content, *, target, context) -> bytes`:

- **`deterministic`** — built-in rule transforms (frontmatter strip/rename, AGENTS.md template
  concatenation). Used when a registered rule matches; pure and reproducible.
- **`agent`** (the default for semantic cases) — builds a prompt and pipes it to the **user's
  configured agent command** (e.g. `claude -p`, `codex exec`, `llm`), captures stdout as the
  transformed content. agentry embeds **no** model or key; it orchestrates the user's CLI.
  Configured in `.agentry.yml` (e.g. `transform: { command: ["claude", "-p"] }`).

The seam signature is generalized from `(ComponentType, dict) -> dict` to carry **content +
target + context** (composition needs the *set* of inputs and the destination format), since
markdown/AGENTS.md emit isn't a single-dict transform.

### 3. Confirmation + gating (always)

- Gated behind **`--allow-transform`** (sibling of `--allow-run`); absent ⇒ transform components
  are skipped with a warning, install proceeds for everything else.
- Before writing, show a **preview/diff** of the proposed output and require confirmation
  (auto-yes via `--yes` for CI authoring). The user sees exactly what the agent produced.
- The exact agent command is printed before it runs (same contract as `generate`).

### 4. First concrete use case: AGENTS.md emit

A built-in transform that composes selected components (skills/agents/instructions) into a
project-root `AGENTS.md` for the universal `agents` target (which today is skills-only). The
deterministic provider does a structured concatenation (headings + bodies); the agent provider
can instead *synthesize* a coherent `AGENTS.md`. Output committed, hash-locked, reversible.

---

## Why this is safe to adopt without betraying agentry's identity

- **Untransformed components are untouched** — live-update, never-clobber, idempotent sync all
  stand. Transform is a per-component opt-in.
- **Reproducibility holds** — AI runs at author time; the committed, hash-locked output is what
  CI verifies. `--frozen` never invokes an agent.
- **Still credential-free** — agentry orchestrates the user's agent CLI; no embedded keys, no
  SDK, nothing of agentry runs while the agent does.
- **Consent-first** — gated, previewed, confirmed; the command is printed.

---

## Scope & phasing

- **Phase 1** — the copy-with-rewrite install path + manifest/lock hash + `--allow-transform`
  gate + the **deterministic** provider + AGENTS.md template emit. No LLM yet; proves the
  reversible, hash-locked plumbing end-to-end.
- **Phase 2** — the **`agent`** provider (shell out to the user's CLI), preview/confirm UX, and
  agent-synthesized AGENTS.md / frontmatter translation. This is where the product steer lands.
- **Phase 3** — broaden deterministic rules (per-target frontmatter maps) and let community
  transforms ship like driver overlays (Track C synergy).

---

## Open questions (for review)

1. **Default aggressiveness of the `agent` provider.** Steer says "AI merge as the default way,
   with confirmation, optional." Confirm: default = *offer* the agent path (with preview +
   confirm) whenever a semantic transform is requested, but it is fully opt-out (per component
   and globally), and absent `--allow-transform` nothing runs. Agree?
2. **Where is the agent command configured** — a global `.agentry.yml` `transform.command`, or
   per-target, or auto-detected from installed CLIs? (Lean: global default + per-target override.)
3. **AGENTS.md scope** — compose from which component types (skills + agents + instructions?),
   and one root `AGENTS.md` vs per-directory?
4. **Is Phase 1 worth shipping alone** (deterministic, no LLM) as a reviewable increment, or do
   we want Phases 1+2 together so the AI-merge story lands in one PR?

---

## Decision requested

Approve the **hybrid, opt-in, copy-with-rewrite, hash-locked** design with an **AI-agent
provider (via the user's own CLI) as the default for semantic transforms, gated and confirmed** —
and pick a phasing (1 alone first, or 1+2 together). On approval I'll move to `writing-plans`
for the implementation plan.
