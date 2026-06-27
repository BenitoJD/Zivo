"""Real-data gate metrics — AUC and calibration error (pure, unit-tested)."""

from __future__ import annotations

import math

from app.sim.realdata import auc, expected_calibration_error


def test_auc_perfect_separation() -> None:
    assert auc([0.1, 0.2, 0.8, 0.9], [False, False, True, True]) == 1.0


def test_auc_anti_correlated_is_zero() -> None:
    assert auc([0.1, 0.2, 0.8, 0.9], [True, True, False, False]) == 0.0


def test_auc_single_class_is_nan() -> None:
    assert math.isnan(auc([0.1, 0.2, 0.3], [True, True, True]))


def test_auc_random_is_near_half() -> None:
    scores = list(range(100))
    labels = [i % 2 == 0 for i in range(100)]
    assert 0.4 < auc(scores, labels) < 0.6


def test_auc_handles_ties_with_average_ranks() -> None:
    # All identical scores → no discrimination → 0.5.
    assert auc([0.5, 0.5, 0.5, 0.5], [True, False, True, False]) == 0.5


def test_ece_perfect_calibration_is_zero() -> None:
    probs = [0.0] * 50 + [1.0] * 50
    labels = [False] * 50 + [True] * 50
    assert expected_calibration_error(probs, labels) == 0.0


def test_ece_fully_overconfident_is_one() -> None:
    assert expected_calibration_error([1.0] * 100, [False] * 100) == 1.0


def test_ece_empty_is_nan() -> None:
    assert math.isnan(expected_calibration_error([], []))
