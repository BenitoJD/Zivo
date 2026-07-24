"""Self-improving loop: retire / flag MCQ items via Item Health Engine.

Orchestration only: load empirical stats, call ``evaluate_item_health``, act.
SQL must not duplicate the engine's retire thresholds (holy grail / ADR 0004).
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI
from app.services.item_health import evaluate_item_health
from app.services.quality_evaluation import EMPIRICAL_MIN_EXPOSURE

logger = logging.getLogger(__name__)


def retire_broken_items(db: Session) -> int:
    """Retire (and flag) items per Item Health Engine. Returns retired count."""
    settings = get_settings()
    if not settings.item_self_improve_enabled:
        return 0
    try:
        metric = concept_id(db, ANSWER_CORRECT_METRIC_URI)
    except ValueError:
        return 0  # vocabulary not seeded — nothing to do

    # Wide net: exposure floor only. Engine decides keep|flag|retire.
    min_exposure = int(settings.item_retire_min_exposure or EMPIRICAL_MIN_EXPOSURE)
    max_rate = getattr(settings, "item_retire_max_correct_rate", None)

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
            """
        ),
        {
            "metric": metric,
            "min_exposure": min_exposure,
        },
    ).mappings().all()

    retire_ids: list[str] = []
    flag_ids: list[str] = []
    for row in rows:
        kwargs: dict = {
            "p_correct": float(row["p_correct"]),
            "n_exposure": int(row["n_exposure"]),
            "min_exposure": min_exposure,
        }
        if max_rate is not None:
            kwargs["max_broken_rate"] = float(max_rate)
        health = evaluate_item_health(**kwargs)
        if health.action == "retire":
            retire_ids.append(str(row["aid"]))
        elif health.action == "flag":
            flag_ids.append(str(row["aid"]))

    if flag_ids:
        # Persist soft CTT flag on assertion payload (reversible; still active).
        db.execute(
            text(
                """
                UPDATE intel.assertion
                SET payload = COALESCE(payload, '{}'::jsonb)
                    || jsonb_build_object('item_health', 'flag', 'item_health_version', 'qb.health.v1')
                WHERE id = ANY(:ids)
                """
            ),
            {"ids": list(flag_ids)},
        )
        logger.info("item self-improve: flagged %d items", len(flag_ids))

    if not retire_ids:
        if flag_ids:
            db.commit()
        return 0

    db.execute(
        text("UPDATE intel.assertion SET status = 'retired' WHERE id = ANY(:ids)"),
        {"ids": list(retire_ids)},
    )
    db.commit()
    logger.info(
        "item self-improve: retired %d broken items (exposure>=%d)",
        len(retire_ids),
        min_exposure,
    )
    return len(retire_ids)
