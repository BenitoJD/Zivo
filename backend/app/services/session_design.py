"""Session Design Engine - soft serve-session length + pool schedule thresholds.

Design: docs/SESSION_DESIGN_ENGINE.md
Version: qb.session.v1
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.question_budget import SESSION_SOFT

SESSION_VERSION = "qb.session.v1"
SESSION_SOFT_DEFAULT = SESSION_SOFT

# Serve-pipeline schedule (ops thresholds owned here; orchestration reads only).
# Env overrides keep load-test knobs without scattering policy in question_pool.
import os as _os

READY_LOW_WATER = int(_os.getenv("ZIVO_READY_LOW_WATER", "8"))
TRANSITION_PREFETCH_RATIO = float(_os.getenv("ZIVO_TRANSITION_PREFETCH_RATIO", "0.45"))
TRANSITION_GENERATION_RATIO = float(_os.getenv("ZIVO_TRANSITION_GENERATION_RATIO", "0.15"))
EAGER_TRIAGE_LOOKAHEAD = int(_os.getenv("ZIVO_EAGER_TRIAGE_LOOKAHEAD", "5"))


@dataclass(frozen=True)
class SessionPlan:
    n_session: int
    soft_cap: int
    policy_version: str = SESSION_VERSION


@dataclass(frozen=True)
class ServeScheduleVerdict:
    refill_now: bool
    prefetch_transition: bool
    generate_next_page: bool
    policy_version: str = SESSION_VERSION


def plan_session(
    remaining_doc_items: int,
    *,
    soft_cap: int = SESSION_SOFT_DEFAULT,
    mode: str = "learn",
) -> SessionPlan:
    soft = max(1, int(soft_cap))
    # Test sessions allow a slightly longer soft slice.
    if (mode or "learn").lower() == "test":
        soft = max(soft, 25)
    rem = max(0, int(remaining_doc_items))
    return SessionPlan(n_session=min(rem, soft) if rem else 0, soft_cap=soft)


def evaluate_serve_schedule(
    *,
    ready_count: int | None = None,
    answered_on_page: int | None = None,
    page_budget: int | None = None,
    low_water: int = READY_LOW_WATER,
    prefetch_ratio: float = TRANSITION_PREFETCH_RATIO,
    generation_ratio: float = TRANSITION_GENERATION_RATIO,
) -> ServeScheduleVerdict:
    """When to refill the ready pool / prefetch next page / kick early generation."""
    refill = False
    if ready_count is not None:
        refill = int(ready_count) < max(0, int(low_water))
    prefetch = False
    generate = False
    if (
        answered_on_page is not None
        and page_budget is not None
        and int(page_budget) > 0
    ):
        ratio = int(answered_on_page) / int(page_budget)
        prefetch = ratio > float(prefetch_ratio)
        generate = ratio >= float(generation_ratio)
    return ServeScheduleVerdict(
        refill_now=refill,
        prefetch_transition=prefetch,
        generate_next_page=generate,
    )
