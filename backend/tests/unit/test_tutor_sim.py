"""Acceptance gates for the adaptive tutor — reproducible, DB-free, CI-enforced.

These exercise the REAL calibrator (app.services.calibration) and selector
(app.services.selection) through the replay harness, so a green test is direct
evidence the shipping tutor adapts — not a parallel reimplementation.

  • Gate 1 — parameter recovery: online Elo recovers planted item difficulty.
  • Gate 2 — adaptation efficacy: difficulty_edge beats sequence on mastery gained
    per question, while holding served items in the productive-struggle band.
"""

from __future__ import annotations

import pytest

from app.sim.harness import adaptation_efficacy, recover_parameters


# ---- Gate 1: parameter recovery (difficulty Spearman >= 0.9) ----------------


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_gate1_recovers_item_difficulty(seed: int) -> None:
    m = recover_parameters(n_learners=300, n_items=60, seed=seed)
    assert m["difficulty_spearman"] >= 0.9, m
    # Learner ability is recovered on the same scale (a bonus the gate doesn't require).
    assert m["ability_spearman"] >= 0.9, m


# ---- Gate 2: adaptation efficacy --------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_gate2_edge_beats_sequence_in_the_band(seed: int) -> None:
    m = adaptation_efficacy(n_items=80, n_questions=40, n_learners=200, seed=seed)
    # Clear margin: difficulty_edge teaches markedly more per question than sequence.
    assert m["mastery_ratio"] >= 1.5, m
    assert m["edge_mastery_per_q"] > m["sequence_mastery_per_q"], m
    # Served questions stay in the productive-struggle band (~0.70–0.85 success).
    assert 0.70 <= m["edge_mean_success"] <= 0.85, m
