"""Item Health / Bank Hygiene Engine — keep / flag / retire.

Design: docs/ITEM_HEALTH_ENGINE.md
Version: qb.health.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.quality_evaluation import (
    EMPIRICAL_EASY_P,
    EMPIRICAL_HARD_P,
    EMPIRICAL_MAX_BROKEN_RATE,
    EMPIRICAL_MIN_EXPOSURE,
    EMPIRICAL_MIN_RPBIS,
    evaluate_empirical,
)

HEALTH_VERSION = "qb.health.v1"
HealthAction = Literal["keep", "flag", "retire"]


@dataclass(frozen=True)
class HealthVerdict:
    action: HealthAction
    codes: tuple[str, ...]
    policy_version: str = HEALTH_VERSION


def evaluate_item_health(
    *,
    p_correct: float,
    n_exposure: int,
    r_pbis: float | None = None,
    min_exposure: int = EMPIRICAL_MIN_EXPOSURE,
    max_broken_rate: float = EMPIRICAL_MAX_BROKEN_RATE,
) -> HealthVerdict:
    emp = evaluate_empirical(
        p_correct=p_correct,
        n_exposure=n_exposure,
        r_pbis=r_pbis,
        min_exposure=min_exposure,
        max_broken_rate=max_broken_rate,
    )
    codes = tuple(emp.flaw_codes)
    if emp.decision == "fail":
        return HealthVerdict("retire", codes)
    if emp.decision == "revise" or any(
        c.startswith("empirical_") for c in codes
    ):
        # Soft CTT flags (too easy/hard / weak rpbis) → flag, not auto-retire.
        if "empirical_likely_broken" in codes:
            return HealthVerdict("retire", codes)
        return HealthVerdict("flag", codes)
    # Extra soft bands even when empirical passes.
    if n_exposure >= min_exposure and (
        p_correct >= EMPIRICAL_EASY_P or p_correct <= EMPIRICAL_HARD_P
    ):
        return HealthVerdict("flag", codes or ("empirical_extreme_p",))
    if (
        r_pbis is not None
        and n_exposure >= min_exposure
        and r_pbis < EMPIRICAL_MIN_RPBIS
        and r_pbis >= 0
    ):
        return HealthVerdict("flag", codes or ("empirical_weak_discrimination",))
    return HealthVerdict("keep", codes)
