# Document what-is; track divergences as dated debt to enforce

**Date:** 2026-06-27 · **Status:** accepted

[docs/CONVENTIONS.md](../CONVENTIONS.md) is **descriptive**: it records what the best
code already does, with a `file:line` proof for each rule. Where the codebase does not
yet uniformly follow a rule, we do not pretend otherwise and we do not write the rule
as if it were enforced — we list the divergence here, with its current state and its
target, and close it in small behavior-preserving slices. A rule becomes "hard" only
once it is machine-enforced.

**Why.** A constitution that contradicts the code teaches the wrong thing (exactly how
the stale `.cursor/skills` copies misled agents). Listing debt openly keeps the doc
trustworthy and gives each cleanup a definition of done.

## Open divergences (close in small, safe slices)

1. **No Python lint/format config.** Target: add `ruff` (format + a conservative lint
   set) under `backend/`, fix violations, then add it to CI. Current: none.
2. **No Python type checking.** Target: add `mypy` (start lenient, tighten), then CI.
   Current: none.
3. **Frontend `lint` not gated.** CI runs `npm run build` only
   ([.github/workflows/ci.yml](../../.github/workflows/ci.yml)); `npm run lint` reports
   pre-existing errors. Target: fix them, add `npm run lint` to CI.
4. **Broad excepts on the core path.** ~54 `except Exception` blocks in `backend/app`;
   some are legitimate best-effort (the pattern in
   `app/services/document_learn_state.py:39`), some swallow core-path errors. Target:
   narrow the core-path ones. No new core-path broad excepts.
5. **Oversized modules.** `app/services/question_pool.py` is ~1365 lines. Target: split
   along its seams (pool refill, page coverage, transition prefetch, answer recording)
   into focused modules with unchanged behavior.
6. **Two test trees.** `backend/tests/` (small) and `backend/tests_citepage/` (large).
   Target: one convention — `unit/` (DB-free, CI-gated) and `integration/` (DB-gated,
   self-skipping) under a single tree.
7. **Dead code.** Target: remove unreferenced helpers/modules as they are found during
   the slices above; never in a big-bang pass.

Each closed item updates [docs/CONVENTIONS.md](../CONVENTIONS.md) (move the rule from
"not enforced" to "enforced") in the same PR.

**Considered and rejected.** A big-bang refactor that fixes all of the above at once —
rejected because it would change behavior broadly and be unreviewable; the
non-negotiable is small, safe, behavior-preserving slices.
