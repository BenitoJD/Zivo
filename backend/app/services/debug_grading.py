"""Grade debug diagnostic steps and record measurements."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.intel import _concept_id

logger = logging.getLogger(__name__)

_DEBUG_UNDERSTOOD_URI = "/vocab/metric/debug.understood"


def grade_step(
    payload: dict[str, Any],
    step_key: str,
    choice_index: int,
) -> dict[str, Any]:
    steps = payload.get("steps") or []
    step = None
    for s in steps:
        if isinstance(s, dict) and str(s.get("key")) == step_key:
            step = s
            break
    if step is None and steps:
        step = steps[0] if isinstance(steps[0], dict) else None
    if step is None:
        return {"correct": False, "correct_index": 0, "explanation": ""}
    correct_index = int(step.get("correct_index", 0))
    return {
        "correct": choice_index == correct_index,
        "correct_index": correct_index,
        "explanation": str(step.get("explanation") or ""),
        "step_key": step_key,
    }


def record_debug_understood(
    db: Session,
    *,
    assertion_id: uuid.UUID,
    subject_entity_id: uuid.UUID | None,
    steps_correct: int,
    steps_total: int,
) -> None:
    if subject_entity_id is None or steps_total <= 0:
        return
    score = steps_correct / steps_total
    value_json = {
        "steps_correct": steps_correct,
        "steps_total": steps_total,
    }
    try:
        db.execute(
            text(
                """
                INSERT INTO intel.measurement (
                  metric_concept_id, subject_entity_id, source_assertion_id,
                  value_numeric, value_json, observed_at
                )
                VALUES (:metric_id, :entity_id, :assertion_id, :score,
                        CAST(:value_json AS jsonb), now())
                ON CONFLICT (subject_entity_id, source_assertion_id, metric_concept_id)
                  WHERE subject_entity_id IS NOT NULL AND source_assertion_id IS NOT NULL
                  DO UPDATE SET
                    value_numeric = GREATEST(intel.measurement.value_numeric, EXCLUDED.value_numeric),
                    value_json = EXCLUDED.value_json,
                    observed_at = EXCLUDED.observed_at
                """
            ),
            {
                "metric_id": _concept_id(db, _DEBUG_UNDERSTOOD_URI),
                "entity_id": subject_entity_id,
                "assertion_id": assertion_id,
                "score": score,
                "value_json": json.dumps(value_json),
            },
        )
    except Exception:
        logger.warning("debug measurement insert failed for %s", assertion_id, exc_info=True)
