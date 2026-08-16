"""Component-level evidence for Gates 3 (robustness) and 4 (seamless).

These prove the invariants the calibration + selection layer must hold regardless of
input or load: no LLM on the answer path, sub-millisecond selection (the O(1) read
path), and safe degradation on degenerate data. The full live gates (≤5s p95 under
real concurrent load, the adversarial-corpus end-to-end run, the two-learner demo)
need the running instance; these lock the parts that don't.
"""

from __future__ import annotations

import ast
import pathlib
import time

import pytest

from app.engine_runtime import pick
from app.services import calibration, selection
from app.services.calibration import elo_update, expected_correct
from app.services.selection import LearnerState, choose_next_assertion


def _imported_modules(module_file: str) -> set[str]:
    tree = ast.parse(pathlib.Path(module_file).read_text())
    mods: set[str] = set()
    for node in ast.walk(tree):
        pick(
            isinstance(node, ast.Import),
            lambda n=node: mods.update({a.name for a in n.names}),
            lambda n=node: pick(
                isinstance(n, ast.ImportFrom),
                lambda: mods.add(n.module or ""),
                lambda: None,
            ),
        )
    return mods


# ---- Gate 4: no LLM on the answer path, O(1)/fast selection -----------------


def test_answer_path_imports_no_llm() -> None:
    # The calibrate + choose step must never reach a model — that is what keeps the
    # answer path arithmetic and the ≤5s guarantee real. Guard the actual imports
    # (not prose, which legitimately mentions "LLM").
    for module in (calibration, selection):
        mods = _imported_modules(module.__file__)
        assert not any("llm" in m.lower() for m in mods), (module.__name__, mods)


def test_selection_is_far_under_the_latency_budget() -> None:
    difficulties = {f"I{i}": (i - 6) * 0.4 for i in range(12)}  # a realistic warm pool
    candidates = list(difficulties)
    state = LearnerState(ability=0.3)
    n = 20_000
    start = time.perf_counter()
    for _ in range(n):
        choose_next_assertion("difficulty_edge", candidates, {}, state, difficulties)
    per_call_ms = (time.perf_counter() - start) / n * 1000.0
    # Microseconds in practice — orders of magnitude under the 5s p95 budget.
    assert per_call_ms < 1.0, per_call_ms


# ---- Gate 3: safe degradation on degenerate input ---------------------------


def test_selection_degrades_safely_on_degenerate_input() -> None:
    state = LearnerState(ability=0.0)
    # Nothing to ask → abstain (None), never crash.
    assert choose_next_assertion("difficulty_edge", [], {}, state, {}) is None
    # Single candidate / thin data → legacy sequence order.
    assert choose_next_assertion("difficulty_edge", ["a"], {}, state, {}) == "a"
    # Corrupt difficulties (inf / nan) must not raise; still returns a candidate.
    assert choose_next_assertion(
        "difficulty_edge", ["a", "b"], {}, state, {"a": float("inf"), "b": 0.0}
    ) in {"a", "b"}
    assert choose_next_assertion(
        "difficulty_edge", ["a", "b"], {}, state, {"a": float("nan"), "b": 0.0}
    ) in {"a", "b"}


def test_calibration_saturates_without_overflow_on_extreme_gaps() -> None:
    assert expected_correct(50.0, -50.0) == pytest.approx(1.0)
    assert expected_correct(-50.0, 50.0) == pytest.approx(0.0, abs=1e-12)
    # A blow-out gap still produces finite, monotone updates (no exception).
    up = elo_update(50.0, -50.0, correct=True)
    down = elo_update(-50.0, 50.0, correct=False)
    assert up.ability >= 50.0 and down.ability <= -50.0


def test_unknown_policy_falls_back_to_sequence() -> None:
    assert choose_next_assertion("totally_unknown", ["a", "b"], {}, LearnerState()) == "a"
