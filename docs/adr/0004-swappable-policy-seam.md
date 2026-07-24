# Era-guess algorithms sit behind a swappable policy seam

**Date:** 2026-06-27 · **Status:** accepted

When an algorithm is an era-guess — a ranking, a metric, a next-step selection — it is
placed behind a named function seam that dispatches by a config-named policy and
degrades to the legacy default. Callers depend on the seam, not the algorithm.
Canonical example: `app/services/adaptive_selection.py` (`select_next` /
`choose_next_assertion`), selected by `Settings.selection_policy` (default
`adaptive_v1`) and falling back to `sequence` order; its caller
`app/services/question_pool.select_next_assertion` never names a specific algorithm.
Compat re-exports live in `app/services/selection.py`. Design:
[ADAPTIVE_SELECTION_ENGINE.md](../ADAPTIVE_SELECTION_ENGINE.md).

**Why.** The long-lived loops (generate → ask → observe → choose) must stay stable for
years while the *metric inside them* is replaced as we learn (e.g. selection by
sequence today, by calibrated difficulty tomorrow). A seam lets the metric change in
one file with no caller churn and a guaranteed safe fallback.

**Considered and rejected.** Hardcoding the current best algorithm inline — rejected
because swapping it later would mean editing every call site and risks a regression
with no safe default.

**Consequence.** Each policy must degrade safely (return the legacy choice when it has
no signal), so turning a new policy on can never get the loop stuck.
