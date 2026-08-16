# All branching is a named engine rule table

**Date:** 2026-08-16 · **Status:** accepted · **Supersedes:** the plumbing-stays-services clause of [ADR 0004](0004-swappable-policy-seam.md) for control flow

First-party Python and TypeScript contain no `if` / `elif` / `else` / ternary / comprehension-`if` tokens, including typing-only `if TYPE_CHECKING:` and Alembic revisions. Product policy and plumbing (auth, HTTP mapping, parse detect, job lifecycle, LLM failover, presence) are named engines whose implementations are rule tables. Callers load signals, call `evaluate_*`, and `apply()` the verdict. The only control-flow primitive is [backend/app/engine_runtime.py](../../backend/app/engine_runtime.py) (`first_match`, `apply`, `choose`, `pick`, `run_steps`), which itself has no `if` keyword. Enforcement: `scripts/scan-no-if.py` in ship-gates.

**Why.** The holy grail is engines, not if-else at call sites or inside facade bodies. Dict-dispatch and rule tables keep the decision swappable (ADR 0004) while making the `if` token illegal.

**Considered and rejected.** Keep `if` inside engine facades (localizes decisions but still is if-else). One-line engine per `if` (fails the deletion test). Phased allowlists (rejected for this campaign: scanner must reach zero).
