"""Gate 4 — seamless under load (reproducible from the harness, no live app).

The gate: next-question selection stays well under the ≤5s p95 budget with many
concurrent simulated learners, no LLM on the answer path (see test_tutor_invariants),
and calibration is O(1). These run the REAL selector/calibrator, so a green test is
evidence about the shipping answer path.
"""

from __future__ import annotations

from app.sim.harness import (
    calibration_per_event_seconds,
    percentile,
    selection_latencies,
)


def test_gate4_selection_p95_under_concurrent_learners() -> None:
    latencies = selection_latencies(n_workers=16, calls_per_worker=500, pool_size=12, seed=0)
    p95 = percentile(latencies, 0.95)
    # Budget is 5s; pure-arithmetic selection runs in microseconds. Assert a tight
    # bound (huge headroom) so a catastrophic regression — an accidental LLM/DB call
    # or O(n^2) blow-up on the hot path — fails loudly.
    assert p95 < 0.5, f"p95={p95 * 1000:.3f}ms over {len(latencies)} concurrent selections"


def test_gate4_selection_stays_fast_on_an_oversized_pool() -> None:
    # Far larger than a real warm pool (~12); still far under the budget.
    latencies = selection_latencies(n_workers=8, calls_per_worker=50, pool_size=2000, seed=1)
    assert percentile(latencies, 0.95) < 1.0


def test_gate4_calibration_is_o1_per_event() -> None:
    small = calibration_per_event_seconds(10_000, seed=0)
    large = calibration_per_event_seconds(80_000, seed=0)
    # Constant work per update: an 8x longer stream costs ~the same per event, and the
    # absolute cost is tiny. (elo_update reads/writes two dict entries + arithmetic.)
    assert large < 1e-4, f"{large * 1e6:.2f}us/event"
    assert large < small * 4 + 1e-5, f"small={small * 1e6:.2f}us large={large * 1e6:.2f}us"
