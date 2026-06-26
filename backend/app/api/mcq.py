"""MCQ grading — fast path + measurement persistence."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.graphs.mcq_graph import grade_mcq
from app.models import Account, Document
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest import can_access_document
from app.services.guest_session import guest_session_for_read
from app.services.question_pool import record_answer, save_confirmed_answer
from app.services.rate_limit import rate_limit_dependency

router = APIRouter()


class GradeIn(BaseModel):
    assertion_id: uuid.UUID
    choice_index: int
    mode: str = "learn"
    latency_ms: int | None = None
    confidence: int | None = None


class AckIn(BaseModel):
    assertion_id: uuid.UUID


def _require_actor(user: Account | None, guest_id: str | None) -> None:
    if not user and not guest_id:
        raise HTTPException(status_code=401, detail="Authentication required")


def _resolve_assertion_artifact(
    db: Session,
    assertion_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
) -> uuid.UUID:
    row = db.execute(
        text("SELECT payload->>'artifact_id' AS artifact_id FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).first()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Not found")
    artifact_id = uuid.UUID(str(row[0]))
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Not found")
    return artifact_id


@router.post("/acknowledge", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
def acknowledge(
    body: AckIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Mark a question answered so learn-queue advances (idempotent)."""
    _require_actor(user, guest_id)
    artifact_id = _resolve_assertion_artifact(db, body.assertion_id, user, guest_id)
    record_answer(db, artifact_id, body.assertion_id)
    return {"ok": True}


@router.post("/grade", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
def grade(
    body: GradeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    _require_actor(user, guest_id)
    artifact_id = _resolve_assertion_artifact(db, body.assertion_id, user, guest_id)
    result = grade_mcq(db, body.assertion_id, body.choice_index)

    # Record the answer event for calibration. Logged-in users resolve to their
    # account entity; anonymous guests resolve to a stable per-guest entity so
    # their practice answers still feed intel.measurement → intel.projection.
    subject_entity_id = _resolve_subject_entity(db, user, guest_id)
    learner_ability: float | None = None
    if subject_entity_id is not None:
        value_json = {"choice_index": body.choice_index}
        if guest_id and not user:
            value_json["guest_id"] = guest_id
            value_json["mode"] = body.mode
        if body.latency_ms is not None:
            value_json["latency_ms"] = body.latency_ms
        if body.confidence is not None:
            value_json["confidence"] = body.confidence
        db.execute(
            text(
                """
                INSERT INTO intel.measurement (
                  metric_concept_id, subject_entity_id, source_assertion_id,
                  value_numeric, value_json, observed_at
                )
                SELECT c.id, :subject_entity_id, :assertion_id, :correct,
                       CAST(:value_json AS jsonb), now()
                FROM intel.concept c
                WHERE c.uri = '/vocab/metric/answer.correct'
                LIMIT 1
                """
            ),
            {
                "subject_entity_id": subject_entity_id,
                "assertion_id": body.assertion_id,
                "correct": 1 if result.get("correct") else 0,
                "value_json": json.dumps(value_json),
            },
        )
        # Calibrate from the outcome: one O(1) Elo step (no LLM) updating this
        # learner's ability and this item's difficulty on a shared scale, so the
        # next question can be chosen at their edge. Same DB txn as the signal row.
        if get_settings().calibration_enabled:
            from app.services.calibration import record_outcome

            calibration = record_outcome(
                db,
                subject_entity_id=subject_entity_id,
                assertion_id=body.assertion_id,
                correct=bool(result.get("correct")),
            )
            learner_ability = calibration.ability
        db.commit()

    save_confirmed_answer(
        db,
        artifact_id,
        body.assertion_id,
        choice_index=body.choice_index,
        correct=bool(result.get("correct")),
        learner_ability=learner_ability,
    )
    record_answer(db, artifact_id, body.assertion_id)

    return result


def _resolve_subject_entity(db: Session, user: Account | None, guest_id: str | None) -> uuid.UUID | None:
    """Resolve the measurement subject for an answer event.

    Logged-in users → their linked intel.entity (qb.account_entity).
    Anonymous guests → a stable per-guest concept entity (lazily created), so
    their practice answers still feed calibration without an account. Returns
    None only if neither identity is available.
    """
    if user:
        row = db.execute(
            text("SELECT entity_id FROM qb.account_entity WHERE account_id = :id"),
            {"id": user.id},
        ).first()
        return row[0] if row else None

    if not guest_id:
        return None

    from app.repositories.intel import get_or_create_concept_entity

    # Stable canonical_uri per guest; type 'concept' keeps it in the graph without
    # needing a 'person' type. The guest_id is the identity for calibration.
    return get_or_create_concept_entity(db, f"guest:{guest_id}", f"Guest {guest_id[:8]}")
