# Competitive Advantage over Microsoft apm — Strategy & Roadmap

*Design spec — 2026-06-30. Companion to the competitive analysis in
[`docs/cm-an-apm-2026-06-30/`](../../cm-an-apm-2026-06-30/).*

---

## Context

agentry and Microsoft **apm** occupy the same category — "a package manager for AI-agent
context" (skills, MCP servers, hooks, prompts installed into coding harnesses). apm is far
more mature: v0.23.0, ~3000 stars, 67 releases, in the Microsoft OSS org, with governance,
a marketplace, and broad distribution. agentry is alpha 0.1.0.

A head-on feature race is unwinnable. But the competitive report establishes a **structural**
thesis: agentry is built on two design choices apm *cannot adopt without contradicting its own
value promise*, plus two supporting properties.

1. **Live-update symlinks.** Edit a skill once, every harness sees it instantly — no recompile.
   apm's promise is the opposite: a deterministic, git-diffable *compiled* artifact. It cannot
   offer live-update without betraying that promise.
2. **Data-driven universality.** Targets are **open strings, not a closed code enum**
   (`target_profiles` in `.agentry.yml`; a built-in driver is a ~25-line `TargetSpec`). A user
   can support *any* agent — even an internal one — in YAML, with no fork, PR, or release wait.
   apm's targets are compiler code; adding one is a centralized contribution.
3. **Safety / reversibility.** `agy sync` never destroys what it didn't create (symlinks removed
   only if they point into `.agentry/`; merges delete only the keys they recorded). Safe to try
   in a repo already full of hand-tuned config.
4. **Simplicity.** One idempotent command (`agy sync`), ~4k LOC, few concepts — versus apm's
   install + compile + run + policy + audit + marketplace surface.

**Committed posture.** Win the developer's daily authoring loop and the long tail of harnesses
(DX + universality); interoperate with apm on open standards rather than fighting it; add
governance only later, as an *optional* layer — never the reverse.

**Out of scope (deliberately deferred).** Policy engine, SARIF audit, transitive-MCP trust
gating, hosted marketplace. This is apm's moat — not a battle for an alpha to pick now.

**Intended outcome.** A roadmap that (a) makes the structural moats visible and undeniable,
(b) erases the cheap, embarrassing parity gaps, (c) builds the single highest-leverage *unbuilt*
moat (shareable `target_profiles`), and (d) neutralizes apm's "standards-based" talking point
via interop.

---

## Strategic frame

> **agentry = the developer's daily driver** — the live local dev loop for agent context.
> **apm = the enterprise governance & distribution layer.**

Messaging pillars (each maps to a structural moat):

- **"Edit once. Every agent sees it."** → live-update loop
- **"Any agent. Even yours."** → data-driven universality
- **"It never touches what you wrote."** → safety / reversibility
- **"One command. `agy sync`."** → simplicity

Who we win first: individual developers and small teams authoring across multiple harnesses;
polyglot-harness shops (Claude + Cursor + an internal CLI); and teams with an **in-house
harness** apm structurally cannot serve without a fork — exclusive territory.

---

## Roadmap

Four tracks. A and B run in parallel (cheap, high-visibility); C is the strategic centerpiece;
D is the durability hedge. Governance is explicitly *Track E — later, optional*.

### Track A — Make the moats undeniable & visible (DX proof)

The moats only count if buyers *see* them. Mostly demonstration and docs; low code.

- **A1 — "Time to first synced skill" demo.** An asciinema/GIF showing edit-once-see-everywhere
  across two harnesses live. Audit the first-run path (`agy init` → `agy add` → `agy sync`) for
  sharp edges. *(report Q13 — DX is the conversion surface.)*
- **A2 — Reproducibility / safety positioning.** Document the two invariants and `agy status`
  (read-only drift) as the credible *trust floor* that substitutes for apm's heavy security.
- **A3 — Publish benchmarks.** Cold sync, warm re-sync (no-op), large-graph. The symlink model
  should *win* warm re-sync; measure it and say so. *(report Q6.)*
- **A4 — Visible release cadence + changelog** to counter the "0.1.0, is it alive?" signal.

### Track B — Close the cheap parity gaps (data + packaging, ~1 sprint)

All low-complexity; the architecture pre-paid for them. Sequenced for visible impact.

- **B1 — Copilot + Kiro built-in targets** *(P0 / P1, Low)*. New `src/agentry/drivers/copilot.py`
  and `kiro.py` modeled on [`drivers/cursor.py`](../../../src/agentry/drivers/cursor.py)
  (~25-line `TargetSpec`); register the `DRIVER` const in
  [`drivers/__init__.py`](../../../src/agentry/drivers/__init__.py) `BUILTIN_DRIVERS`; add the
  `Target` constants and `BUILTIN_TARGET_NAMES` entries in
  [`models.py`](../../../src/agentry/models.py). The work is confirming each tool's on-disk
  layout, not engine changes.
- **B2 — Universal `AGENTS.md` / Agent-Skills target** *(P0, Low–Med)*. New
  `src/agentry/drivers/agents_md.py`. Pure `TargetSpec` placement if it places discrete files;
  edges to Med only if it must *compose* multiple components into one `AGENTS.md` (touches merge
  semantics). One target, many tools — highest universality leverage.
- **B3 — `--frozen` lockfile-only install** *(P0, Low)*. Thread a flag through
  [`cli.py`](../../../src/agentry/cli.py) `sync_command`/`install_command` →
  [`reconcile.py`](../../../src/agentry/reconcile.py) `sync(update=…)` →
  [`deps.py`](../../../src/agentry/deps.py) `resolve_graph`.
  [`resolver.resolve`](../../../src/agentry/resolver.py) already accepts a `pinned` SHA, so
  "frozen" = require pinned, never re-resolve, error on drift. Unblocks CI credibility.
- **B4 — Homebrew tap + Scoop manifest** *(P0, Low)*. Binaries and `install.sh`/`install.ps1`
  already exist (PyInstaller wired in `pyproject.toml`). Release metadata + checksums only — no
  source change.
- **B5 — Document additional git hosts** *(P1, Low)*. `resolver.py` already clones any git URL;
  add GitLab `/-/blob/` plus Bitbucket/Azure raw-URL niceties in
  [`registry.py`](../../../src/agentry/registry.py) `_normalize_url`, a test matrix, and a README
  line.
- **B6 — GitHub Action** *(P1, Low)* wrapping `agy sync --frozen` (trivial once B3 lands).

### Track C — Shareable `target_profiles` / community driver layer (the centerpiece)

The report names this *"the single highest-leverage unbuilt idea."* It converts agentry's biggest
liability — a small team chasing 7+ fast-moving harnesses alone — into its biggest asset: curating
community-supplied driver patches. It makes universality *compounding*.

- **C1 — Distributable driver overlays.** Let a `target_profiles` definition be authored, shared,
  and installed like a source/catalog entry, not just hand-edited in one repo. Build on the
  existing resolution in [`targets.py`](../../../src/agentry/targets.py) (`resolve_targets`,
  `_apply_profile`) and the catalog contract in `registry.py`.
- **C2 — Promotion path.** When `agy` hits an unresolved target, point the user at installable
  community profiles instead of a dead-end warning.
- **C3 — Curated, *small* community catalog** of driver overlays — quality over breadth.

This is where the moat becomes durable: apm cannot match decentralized, code-free target authoring
without abandoning its compiler model.

### Track D — Interop with apm (neutralize "standards-based", widen the funnel)

- **D1 — `agy import apm` bridge.** A one-shot translator reading apm's `apm.yml` → agentry
  `sources` + `components`. Lets a team evaluate agentry on a real apm repo in seconds.
- **D2 — Consume apm packages.** Recognize apm's `.apm/` on-disk layout so agentry installs
  apm-published components without the author republishing. "A handful of lines."
- **D3 — Emit `AGENTS.md` via the reserved `transform` seam.** `TransformFn` already exists,
  unimplemented (`transform=None`), in [`drivers/base.py`](../../../src/agentry/drivers/base.py).
  A real emit path makes the two tools format-compatible by construction and hedges the
  "ecosystem converges on AGENTS.md" risk. Highest-ambition; sequence after D1/D2.

### Track E — Governance (LATER, OPTIONAL — explicitly deferred)

Pursue only if/when an enterprise wedge is *deliberately* chosen: a policy engine
(`apm-policy.yml`-equivalent tighten-only inheritance), SARIF `audit --ci`, transitive-MCP trust
gating, content scanning. Add *on top of* an already-loved daily driver — never first.

---

## Sequencing

1. **Sprint 1 (parallel):** Track B (B1 → B6) + Track A1 / A4 — erase visible parity, prove DX.
2. **Sprint 2:** Track C (C1 → C3) + A2 / A3 — build the durable, compounding moat + benchmarks.
3. **Sprint 3+:** Track D (D1 → D3) — interop + standards emit.
4. **Deferred:** Track E, per an explicit enterprise decision only.

---

## Critical files (reuse-first; confirmed via codebase read)

| Purpose | File | Notes |
|---|---|---|
| Target/driver template | [`src/agentry/drivers/cursor.py`](../../../src/agentry/drivers/cursor.py) | ~25-line `TargetSpec` pattern for B1/B2 |
| `TargetSpec` shape | [`src/agentry/spec.py`](../../../src/agentry/spec.py) | `link`/`copy`/`merge`/`link_merge` per `ComponentType` |
| Driver registry | [`src/agentry/drivers/__init__.py`](../../../src/agentry/drivers/__init__.py) | `BUILTIN_DRIVERS` registration line |
| Target constants | [`src/agentry/models.py`](../../../src/agentry/models.py) | `Target` + `BUILTIN_TARGET_NAMES`; `Config.target_profiles` |
| Profile resolution | [`src/agentry/targets.py`](../../../src/agentry/targets.py) | `resolve_targets`, `_apply_profile` — base for Track C |
| `transform` seam | [`src/agentry/drivers/base.py`](../../../src/agentry/drivers/base.py) | `TransformFn` reserved; base for D3 |
| Frozen flag path | [`cli.py`](../../../src/agentry/cli.py), [`reconcile.py`](../../../src/agentry/reconcile.py), [`deps.py`](../../../src/agentry/deps.py), [`resolver.py`](../../../src/agentry/resolver.py) | thread `--frozen`; `resolve` takes a `pinned` SHA |
| Git-host niceties | [`src/agentry/registry.py`](../../../src/agentry/registry.py) | `_normalize_url` / `parse_repo_url` |
| Distribution | `pyproject.toml`, `install.sh`, `install.ps1` | binaries already built for B4 |

---

## Verification

- **B1 / B2 targets.** Add a fixture component, `agy sync` into a temp project, assert files land
  at the expected paths; `agy status` is clean; `agy remove` reverses cleanly (safety invariant).
  Mirror existing driver tests in `tests/`.
- **B3 `--frozen`.** `agy sync --frozen` succeeds when `.agentry.lock` matches; **fails** when a
  source ref drifts. Add a regression test asserting the drift error.
- **C1 shareable profiles.** Author a profile overlay, install it, confirm a previously
  unresolved target now resolves and installs.
- **D1 import.** Run `agy import apm` against a sample `apm.yml`; assert the generated
  `.agentry.yml` syncs and produces equivalent placement.
- **Full suite.** Existing `pytest` + lint pass; warm re-sync is a verified no-op (idempotence).
- **DX proof (A1 / A3).** Record the demo; capture cold / warm / large-graph timings into this doc.

---

## Success metrics (telemetry-free, per report Q15)

Install-script hits, `agy init` downloads, **count of distinct harnesses users target** (the
universality signal), community driver-overlay contribution rate, and issue/PR response cadence.
Keep metrics telemetry-free to preserve the trust posture.
