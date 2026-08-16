"""Item Health / Bank Hygiene Engine — keep / flag / retire.

Design: docs/ITEM_HEALTH_ENGINE.md
Version: qb.health.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.engine_runtime import Pred, Rule, first_match
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

_RULES = (
    Rule(when=(Pred("emp_fail", "truthy"),), action="retire"),
    Rule(when=(Pred("likely_broken", "truthy"),), action="retire"),
    Rule(when=(Pred("emp_revise", "truthy"),), action="flag"),
    Rule(when=(Pred("has_empirical_code", "truthy"),), action="flag"),
    Rule(
        when=(Pred("exposed", "truthy"), Pred("extreme_p", "truthy")),
        action="flag",
        extras={"codes_fallback": "empirical_extreme_p"},
    ),
    Rule(
        when=(Pred("exposed", "truthy"), Pred("weak_rpbis", "truthy")),
        action="flag",
        extras={"codes_fallback": "empirical_weak_discrimination"},
    ),
    Rule(when=(), action="keep"),
)


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
    signals = {
        "emp_fail": emp.decision == "fail",
        "likely_broken": "empirical_likely_broken" in codes,
        "emp_revise": emp.decision == "revise",
        "has_empirical_code": any(c.startswith("empirical_") for c in codes),
        "exposed": n_exposure >= min_exposure,
        "extreme_p": p_correct >= EMPIRICAL_EASY_P or p_correct <= EMPIRICAL_HARD_P,
        "weak_rpbis": r_pbis is not None and r_pbis < EMPIRICAL_MIN_RPBIS and r_pbis >= 0,
    }
    hit = first_match(_RULES, signals)
    fallback = tuple(filter(None, (hit.extras.get("codes_fallback"),)))
    return HealthVerdict(hit.action, codes or fallback)
