"""Session Design Engine - soft serve-session length.

Design: docs/SESSION_DESIGN_ENGINE.md
Version: qb.session.v1
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.question_budget import SESSION_SOFT

SESSION_VERSION = "qb.session.v1"
SESSION_SOFT_DEFAULT = SESSION_SOFT


@dataclass(frozen=True)
class SessionPlan:
    n_session: int
    soft_cap: int
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
