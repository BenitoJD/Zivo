"""Gate 3 — open-world robustness (reproducible from the harness, no live app).

The adaptive layer only ever sees answer *events* (correct/latency/choice), never raw
documents — so its open-world robustness is whether it survives the degenerate and
adversarial *signal* that diverse/junk content produces: empty pools (nothing to ask),
all-cold items, all-correct/all-wrong streaks, ties, NaN/inf difficulties, huge pools.
It must never crash, must abstain when there is nothing to ask, and must not weaken the
upstream quality gate. (A full document-corpus end-to-end smoke is a separate deploy
check; it does not touch this layer's logic.)
"""

from __future__ import annotations

import math

from app.services.selection import LearnerState, choose_next_assertion
from app.sim.harness import (
    AnswerEvent,
    generate_events,
    replay,
    simulate_population,
)


def test_gate3_survives_adversarial_answer_streams() -> None:
    # Pathological single learner/item patterns must converge to finite estimates
    # (the logistic self-limits: as the rating gap grows, updates shrink).
    patterns = {
        "all_correct": lambda i: True,
        "all_wrong": lambda i: False,
        "alternating": lambda i: i % 2 == 0,
    }
    for name, fn in patterns.items():
        result = replay([AnswerEvent("L", "I", fn(i)) for i in range(5000)])
        assert math.isfinite(result.ability["L"]), name
        assert math.isfinite(result.difficulty["I"]), name

    # A random adversarial population: every estimate stays finite, no crash.
    abilities, difficulties = simulate_population(50, 20, seed=3)
    result = replay(generate_events(abilities, difficulties, seed=4))
    assert all(math.isfinite(v) for v in result.difficulty.values())
    assert all(math.isfinite(v) for v in result.ability.values())


def test_gate3_abstains_when_there_is_nothing_to_ask() -> None:
    # A contentless/junk page yields no items → no fabrication, just abstain.
    assert choose_next_assertion("difficulty_edge", [], {}, LearnerState(ability=0.0), {}) is None


def test_gate3_degenerate_pools_never_crash() -> None:
    state = LearnerState(ability=0.5)
    # Thin data → legacy sequence order.
    assert choose_next_assertion("difficulty_edge", ["a"], {}, state, {}) == "a"
    # All-cold pool.
    assert choose_next_assertion("difficulty_edge", ["a", "b"], {}, state, {}) == "a"
    # Oversized pool, all identical difficulty.
    huge = {f"I{i}": 0.0 for i in range(10_000)}
    assert choose_next_assertion("difficulty_edge", list(huge), {}, state, huge) in huge
    # Corrupt difficulties (NaN/inf) must not raise; still returns a valid candidate.
    weird = {"a": float("nan"), "b": float("inf"), "c": 0.0}
    assert choose_next_assertion("difficulty_edge", ["a", "b", "c"], {}, state, weird) in weird


def test_gate3_quality_gate_is_not_weakened() -> None:
    # Robustness must never come from lowering the bar: every original IWF fatal flaw
    # is still fatal, and the recognition-only axis was added (deepened, not relaxed).
    from app.services.mcq_quality import FATAL_FLAW_CODES

    required = {
        "ambiguous_unclear",
        "more_than_one_correct",
        "implausible_distractors",
        "none_or_all_of_above",
        "unfocused_stem",
        "longest_option_correct",
        "negative_wording",
        "not_grounded",
        "invalid_structure",
        "too_similar_to_prior",
        "meta_page_reference",
        "not_self_contained",
        "recognition_only",
    }
    assert required <= FATAL_FLAW_CODES
