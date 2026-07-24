"""Mastery / Evidence-Stop Engine — when to pause a concept or session.

Design: docs/MASTERY_EVIDENCE_ENGINE.md
Version: qb.mastery.v1
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

MASTERY_VERSION = "qb.mastery.v1"
DEFAULT_MASTERY_POLICY = "elo_proxy_v1"

MASTERY_P = 0.95
MASTERY_MIN_N = 3
SE_TARGET = 0.40
SE_MIN_N = 5

StopReason = Literal["mastery", "se_precision", "min_items", "continue"]


@dataclass(frozen=True)
class StopVerdict:
    stop: bool
    reason: StopReason
    p_mastery: float
    se_theta: float
    n: int
    policy_version: str = MASTERY_VERSION


def p_mastery_from_ability(ability: float, *, threshold_difficulty: float = 0.0) -> float:
    """Logistic P(correct) vs a reference difficulty as a cheap mastery proxy."""
    return 1.0 / (1.0 + math.exp(-(ability - threshold_difficulty)))


def evaluate_stop(
    *,
    ability: float,
    n: int,
    se_theta: float | None = None,
    mode: str = "learn",
    running_info: float | None = None,
) -> StopVerdict:
    n = max(0, int(n))
    p = p_mastery_from_ability(ability)
    if se_theta is None:
        info = float(running_info or 0.0)
        if info <= 0 and n > 0:
            info = 0.2 * n
        se_theta = 1.0 / math.sqrt(max(info, 1e-6))
    mode = (mode or "learn").lower()
    if mode == "test":
        if n < SE_MIN_N:
            return StopVerdict(False, "min_items", p, se_theta, n)
        if se_theta <= SE_TARGET:
            return StopVerdict(True, "se_precision", p, se_theta, n)
        return StopVerdict(False, "continue", p, se_theta, n)
    # Learn: mastery threshold with min evidence.
    if n < MASTERY_MIN_N:
        return StopVerdict(False, "min_items", p, se_theta, n)
    if p >= MASTERY_P:
        return StopVerdict(True, "mastery", p, se_theta, n)
    return StopVerdict(False, "continue", p, se_theta, n)
