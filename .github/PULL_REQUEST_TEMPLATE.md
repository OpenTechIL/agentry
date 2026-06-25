<!-- Thanks for contributing to agentry! Keep PRs small and focused — one behavior change where possible. -->

## What & why

<!-- What does this change and why? Link any related issue: "Closes #123". -->

## Checklist

- [ ] Tests added or updated for the behavior change (`uv run pytest` passes)
- [ ] `uvx ruff check .` and `uvx ruff format --check .` are clean
- [ ] Docs updated where relevant — `docs/architecture.md` is the source of truth for behavior
- [ ] `agy sync` stays **idempotent** and the **safety invariants** hold
      (never touch unmanaged files/links or hand-added config entries)
- [ ] Commit subjects are clear and imperative (e.g. `add cursor mcp target`)

## Notes for reviewers

<!-- Anything reviewers should focus on, trade-offs, or follow-ups. -->
