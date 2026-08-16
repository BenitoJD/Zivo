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
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import choose, pick
from app.models import Account
from app.repositories.intel import concept_id
from app.services.calibration import record_outcome
from app.services.mastery_evidence import evaluate_stop
from app.services.spaced_revisit import plan_revisit

logger = logging.getLogger(__name__)


def resolve_subject_entity(
    db: Session, user: Account | None, guest_id: str | None
) -> uuid.UUID | None:
    """Resolve the measurement subject for a learner (the answer/report identity).

    Logged-in users → their linked intel.entity (qb.account_entity). Anonymous
    guests → a stable per-guest concept entity (lazily created), so their answers
    still feed calibration and the report card without an account. None only if
    neither identity is available.
    """

    def _from_user() -> uuid.UUID:
        from app.repositories.intel import get_or_create_account_entity

        return get_or_create_account_entity(db, user.id, user.username)

    def _from_guest() -> uuid.UUID | None:
        from app.repositories.intel import get_or_create_concept_entity

        return pick(
            not guest_id,
            lambda: None,
            lambda: get_or_create_concept_entity(db, f"guest:{guest_id}", f"Guest {guest_id[:8]}"),
        )

    return pick(bool(user), _from_user, _from_guest)


def answered_assertion_ids(
    db: Session,
    subject_entity_id: uuid.UUID,
    assertion_ids: list[str] | None = None,
) -> set[str]:
    """Assertion ids this learner has already answered (first-attempt signal)."""
    params: dict[str, object] = {
        "entity_id": subject_entity_id,
        "metric_id": concept_id(db, ANSWER_CORRECT_METRIC_URI),
    }
    filter_sql = pick(
        bool(assertion_ids),
        lambda: (params.__setitem__("assertion_ids", assertion_ids), "AND source_assertion_id::text = ANY(:assertion_ids)")[1],
        lambda: "",
    )
    rows = db.execute(
        text(
            f"""
            SELECT source_assertion_id::text AS id
            FROM intel.measurement
            WHERE subject_entity_id = :entity_id
              AND metric_concept_id = :metric_id
              {filter_sql}
            """
        ),
        params,
    ).scalars().all()
    return {str(row) for row in rows}


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
    ability_se: float | None = None
    mastery_stop: bool | None = None
    revisit_hours: float | None = None
    revisit_ease: float | None = None
    revisit_repetitions: int | None = None


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
    prior_interval_hours: float | None = None,
    prior_ease: float | None = None,
    prior_repetitions: int | None = None,
) -> AnswerSignal:
    """Idempotently record one answer; calibrate only when it is genuinely new.

    O(1) and LLM-free. The caller commits.
    """
    value_json: dict[str, object] = {"choice_index": int(choice_index)}
    pick(
        bool(guest_id),
        lambda: (value_json.__setitem__("guest_id", guest_id), value_json.__setitem__("mode", mode)),
        lambda: None,
    )
    pick(latency_ms is not None, lambda: value_json.__setitem__("latency_ms", int(latency_ms)), lambda: None)
    pick(confidence is not None, lambda: value_json.__setitem__("confidence", int(confidence)), lambda: None)

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
            "correct": choose(correct, 1, 0),
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
    ability_se: float | None = None
    mastery_stop: bool | None = None
    revisit_hours: float | None = None
    revisit_ease: float | None = None
    revisit_repetitions: int | None = None

    def _calibrate() -> None:
        nonlocal ability, difficulty, ability_se, mastery_stop
        nonlocal revisit_hours, revisit_ease, revisit_repetitions
        try:
            from app.services.calibration_engine import get_ability

            update = record_outcome(
                db,
                subject_entity_id=subject_entity_id,
                assertion_id=assertion_id,
                correct=correct,
            )
            ability, difficulty = update.ability, update.difficulty
            _rating, n, ability_se = get_ability(db, subject_entity_id)
            stop = evaluate_stop(
                ability=ability, n=n, se_theta=ability_se, mode=mode
            )
            mastery_stop = stop.stop
            plan = plan_revisit(
                last_correct=correct,
                prior_interval_hours=pick(
                    prior_interval_hours is not None,
                    lambda: float(prior_interval_hours),
                    lambda: 24.0,
                ),
                ease=pick(prior_ease is not None, lambda: float(prior_ease), lambda: 2.5),
                repetitions=pick(
                    prior_repetitions is not None,
                    lambda: int(prior_repetitions),
                    lambda: 0,
                ),
            )
            revisit_hours = plan.next_due_hours
            revisit_ease = plan.ease
            revisit_repetitions = plan.repetitions
        except Exception:
            logger.warning(
                "calibration failed for assertion %s; measurement kept", assertion_id, exc_info=True
            )

    pick(inserted and calibrate, _calibrate, lambda: None)

    return AnswerSignal(
        inserted=inserted,
        concept_key=concept_key,
        ability=ability,
        difficulty=difficulty,
        ability_se=ability_se,
        mastery_stop=mastery_stop,
        revisit_hours=revisit_hours,
        revisit_ease=revisit_ease,
        revisit_repetitions=revisit_repetitions,
    )
