"""Self-improving loop: retire MCQ items real learners almost never get right.

An item that nearly everyone answers wrong on their FIRST try — over enough
distinct learners — is far more likely BROKEN (wrong key, ambiguous, untestable)
than merely hard. We flip its assertion status to 'retired' so it stops being
served (reversible; every selection query filters status='active').

Sourced from the immutable intel.measurement first-answer rows (one per
learner+item), so the signal is honest. Conservative + flag-gated: closing the
quality loop without throwing away legitimately-hard content.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI

logger = logging.getLogger(__name__)


def retire_broken_items(db: Session) -> int:
    """Retire items whose measured first-try correct rate is near-zero over a
    minimum exposure. Returns how many were retired (0 when disabled/none)."""
    settings = get_settings()
    if not settings.item_self_improve_enabled:
        return 0
    try:
        metric = concept_id(db, ANSWER_CORRECT_METRIC_URI)
    except ValueError:
        return 0  # vocabulary not seeded — nothing to do

    ids = db.execute(
        text(
            """
            SELECT m.source_assertion_id AS aid
            FROM intel.measurement m
            JOIN intel.assertion a ON a.id = m.source_assertion_id
            WHERE m.metric_concept_id = :metric
              AND a.status = 'active'
            GROUP BY m.source_assertion_id
            HAVING COUNT(*) >= :min_exposure
               AND AVG(m.value_numeric) <= :max_rate
            """
        ),
        {
            "metric": metric,
            "min_exposure": settings.item_retire_min_exposure,
            "max_rate": settings.item_retire_max_correct_rate,
        },
    ).scalars().all()

    if not ids:
        return 0

    db.execute(
        text("UPDATE intel.assertion SET status = 'retired' WHERE id = ANY(:ids)"),
        {"ids": list(ids)},
    )
    db.commit()
    logger.info(
        "item self-improve: retired %d broken items (exposure>=%d, correct_rate<=%.2f)",
        len(ids),
        settings.item_retire_min_exposure,
        settings.item_retire_max_correct_rate,
    )
    return len(ids)
