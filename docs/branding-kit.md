# agentry — Branding Kit

The verbal and visual identity for the project. Keep it consistent across the README,
docs, CLI output, and any site.

## Name

**agentry** — a blend of **agent** + **registry**. (It's also a real word: *agentry*,
the work or office of an agent.) It signals exactly what the tool does: a registry of
agent components you manage like dependencies.

- Package / project name: `agentry`
- CLI command: `agy` (short, fast to type, like `pip` / `npm` / `uv`)
- Always lowercase: `agentry`, `agy` — never "Agentry" mid-sentence, never "AGY".
- Dotfiles: `.agentry.yml`, `.agentry.lock`, `.agentry/`.

## Logo

See [`branding/logo.svg`](branding/logo.svg). The mark is a square bracket pair `[ ]`
(a registry / list) enclosing a chevron `›` (an agent prompt / forward motion):
**“components, listed and ready.”** Use the SVG; don't recolor outside the palette.

Clear space: keep padding ≥ the height of the chevron on all sides. Minimum size: 24px tall.

## Color palette

| Role | Name | Hex | Use |
|---|---|---|---|
| Primary | Agent Indigo | `#5A4FCF` | logo, headings, primary accents |
| Accent | Signal Cyan | `#22D3EE` | links, "enabled" state, highlights |
| Success | Lock Green | `#22C55E` | installed / ok |
| Warning | Drift Amber | `#F59E0B` | drift / disabled / warnings |
| Danger | Remove Red | `#EF4444` | removed / errors |
| Ink | Terminal Ink | `#1E1E2E` | text on light, dark backgrounds |
| Paper | Paper | `#F8FAFC` | light backgrounds |

These map directly to the CLI's Rich styles (see below), so the terminal and the docs
feel like one product.

## CLI output colors

| Meaning | Rich style |
|---|---|
| added / created | `green` (`+`) |
| updated | `yellow` (`~`) |
| removed | `red` (`-`) |
| resolved SHA | `cyan` |
| warning | `yellow` (`!`) |
| dim / secondary | `dim` |

## Typography

- **Docs / web:** a humanist sans for prose (Inter, or system `-apple-system`), and a
  monospace for code (JetBrains Mono / SF Mono / `ui-monospace`).
- **CLI:** whatever the user's terminal provides — design output to read well in plain
  monospace; never rely on color alone (always pair color with a glyph: `+ ~ - !`).

## Tone of voice

agentry talks like a good package manager: **terse, factual, reassuring.**

- State what happened, not what might happen: `+ link .claude/skills/code-reviewer`.
- Lead with the verb. `Added team-skills`, `Removed source gitsrc`.
- Be honest about no-ops: `already up to date`.
- Warnings are specific and actionable, never scary: `target 'opencode' does not support hook — skipped`.
- No exclamation-mark hype, no emoji in command output. Calm and deterministic, like the tool.

## ASCII banner

For splash/help/release notes:

```
   __ _  __ _  ___ _ __ | |_ _ __ _   _
  / _` |/ _` |/ _ \ '_ \| __| '__| | | |
 | (_| | (_| |  __/ | | | |_| |  | |_| |
  \__,_|\__, |\___|_| |_|\__|_|   \__, |
        |___/                     |___/
  AI agent dependencies, managed.   agy
```

## Naming conventions (in product)

- Component reference: `<source>/<type>/<name>` (e.g. `team-skills/skill/code-reviewer`).
- Sources have short lowercase logical names (`team-skills`, not `Team Skills`).
- Verbs in the CLI mirror package managers: `add`, `remove`, `enable`, `disable`,
  `sync`/`install`, `update`, `status`, `list`/`search`.
