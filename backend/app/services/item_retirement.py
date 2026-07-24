"""Self-improving loop: retire MCQ items real learners almost never get right.

An item that nearly everyone answers wrong on their FIRST try — over enough
distinct learners — is far more likely BROKEN (wrong key, ambiguous, untestable)
than merely hard. We flip its assertion status to 'retired' so it stops being
served (reversible; every selection query filters status='active').

Sourced from the immutable intel.measurement first-answer rows (one per
learner+item), so the signal is honest. Conservative + flag-gated: closing the
quality loop without throwing away legitimately-hard content.

Thresholds come from quality_evaluation (CTT empirical stage) so cook gates and
retirement share one policy seam.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI
from app.services.quality_evaluation import (
    EMPIRICAL_MAX_BROKEN_RATE,
    EMPIRICAL_MIN_EXPOSURE,
    evaluate_empirical,
)

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

    min_exposure = int(settings.item_retire_min_exposure or EMPIRICAL_MIN_EXPOSURE)
    max_rate = float(settings.item_retire_max_correct_rate or EMPIRICAL_MAX_BROKEN_RATE)

    rows = db.execute(
        text(
            """
            SELECT m.source_assertion_id AS aid,
                   COUNT(*)::int AS n_exposure,
                   AVG(m.value_numeric)::float AS p_correct
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
            "min_exposure": min_exposure,
            "max_rate": max_rate,
        },
    ).mappings().all()

    ids: list[str] = []
    for row in rows:
        verdict = evaluate_empirical(
            p_correct=float(row["p_correct"]),
            n_exposure=int(row["n_exposure"]),
            min_exposure=min_exposure,
            max_broken_rate=max_rate,
        )
        if verdict.decision == "fail" and "empirical_likely_broken" in verdict.flaw_codes:
            ids.append(str(row["aid"]))

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
        min_exposure,
        max_rate,
    )
    return len(ids)
