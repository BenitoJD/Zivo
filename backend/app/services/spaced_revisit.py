"""Spaced Revisit Engine — simplified SM-2-inspired schedule.

Design: docs/SPACED_REVISIT_ENGINE.md
Version: qb.space.v1
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine_runtime import Pred, Rule, first_match, pick

SPACE_VERSION = "qb.space.v1"

_INTERVAL_RULES = (
    Rule(when=(Pred("reps", "eq", 1),), action="fixed", extras={"hours": 24.0}),
    Rule(when=(Pred("reps", "eq", 2),), action="fixed", extras={"hours": 72.0}),
    Rule(when=(), action="scaled"),
)


@dataclass(frozen=True)
class RevisitPlan:
    next_due_hours: float
    ease: float
    repetitions: int
    policy_version: str = SPACE_VERSION


def _correct_interval(reps: int, prior: float, ease: float) -> float:
    hit = first_match(_INTERVAL_RULES, {"reps": reps})
    return pick(
        hit.action == "fixed",
        lambda: float(hit.extras["hours"]),
        lambda: prior * ease,
    )


def plan_revisit(
    *,
    last_correct: bool,
    prior_interval_hours: float = 24.0,
    ease: float = 2.5,
    repetitions: int = 0,
) -> RevisitPlan:
    ease = max(1.3, float(ease))
    reps = max(0, int(repetitions))
    prior = max(1.0, float(prior_interval_hours))
    reps_out = pick(last_correct, lambda: reps + 1, lambda: 0)
    ease_out = pick(
        last_correct,
        lambda: min(3.0, ease + 0.1),
        lambda: max(1.3, ease - 0.2),
    )
    nxt = pick(
        last_correct,
        lambda: _correct_interval(reps_out, prior, ease_out),
        lambda: 4.0,
    )
    return RevisitPlan(next_due_hours=float(nxt), ease=float(ease_out), repetitions=reps_out)
