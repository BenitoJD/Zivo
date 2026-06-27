"""Idempotent answer-signal recording — the moat's write path.

Every graded answer writes exactly one immutable ``intel.measurement`` row and, when
calibration is enabled, advances the online Elo estimates. The write is idempotent on
``(learner, item, metric)`` via the partial unique index added in migration
``013_measurement_answer_idempotent``: a client retry or a worker crash + replay finds
the row already there, the insert is a no-op, and calibration is skipped — so the same
answer can never be counted twice. The first answer per (learner, item) is the signal.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.intel import concept_id
from app.services.calibration import record_outcome

# One row per answer carries the verdict (value_numeric) plus choice/latency/confidence
# in value_json, under this metric.
ANSWER_CORRECT_METRIC_URI = "/vocab/metric/answer.correct"


@dataclass(frozen=True)
class AnswerSignal:
    """Outcome of recording one answer.

    ``inserted`` is False when this answer was already recorded (retry/replay) — the
    caller must then NOT advance any per-learner estimate, to avoid double-counting.
    """

    inserted: bool
    concept_key: str | None
    ability: float | None = None
    difficulty: float | None = None


def record_answer_signal(
    db: Session,
    *,
    subject_entity_id: uuid.UUID,
    assertion_id: uuid.UUID,
    correct: bool,
    choice_index: int,
    latency_ms: int | None = None,
    confidence: int | None = None,
    mode: str = "learn",
    guest_id: str | None = None,
    calibrate: bool = False,
) -> AnswerSignal:
    """Idempotently record one answer; calibrate only when it is genuinely new.

    O(1) and LLM-free. The caller commits.
    """
    value_json: dict[str, object] = {"choice_index": int(choice_index)}
    if guest_id:
        value_json["guest_id"] = guest_id
        value_json["mode"] = mode
    if latency_ms is not None:
        value_json["latency_ms"] = int(latency_ms)
    if confidence is not None:
        value_json["confidence"] = int(confidence)

    row = db.execute(
        text(
            """
            INSERT INTO intel.measurement (
              metric_concept_id, subject_entity_id, source_assertion_id,
              value_numeric, value_json, observed_at
            )
            VALUES (:metric_id, :entity_id, :assertion_id, :correct,
                    CAST(:value_json AS jsonb), now())
            ON CONFLICT (subject_entity_id, source_assertion_id, metric_concept_id)
              WHERE subject_entity_id IS NOT NULL AND source_assertion_id IS NOT NULL
              DO NOTHING
            RETURNING id
            """
        ),
        {
            "metric_id": concept_id(db, ANSWER_CORRECT_METRIC_URI),
            "entity_id": subject_entity_id,
            "assertion_id": assertion_id,
            "correct": 1 if correct else 0,
            "value_json": json.dumps(value_json),
        },
    ).first()
    inserted = row is not None

    concept_key = db.execute(
        text("SELECT payload->>'primary_concept_key' FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).scalar()

    ability: float | None = None
    difficulty: float | None = None
    if inserted and calibrate:
        update = record_outcome(
            db,
            subject_entity_id=subject_entity_id,
            assertion_id=assertion_id,
            correct=correct,
        )
        ability, difficulty = update.ability, update.difficulty

    return AnswerSignal(
        inserted=inserted, concept_key=concept_key, ability=ability, difficulty=difficulty
    )
