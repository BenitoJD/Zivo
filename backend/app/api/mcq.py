"""MCQ grading — fast path + measurement persistence."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

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

    if user:
        db.execute(
            text(
                """
                INSERT INTO intel.measurement (
                  metric_concept_id, subject_entity_id, source_assertion_id,
                  value_numeric, payload
                )
                SELECT c.id, ae.entity_id, :assertion_id, :correct, CAST(:payload AS jsonb)
                FROM intel.concept c
                LEFT JOIN qb.account_entity ae ON ae.account_id = :account_id
                WHERE c.uri = '/vocab/metric/answer.correct'
                LIMIT 1
                """
            ),
            {
                "assertion_id": body.assertion_id,
                "account_id": user.id,
                "correct": 1 if result.get("correct") else 0,
                "payload": '{"choice_index": %d}' % body.choice_index,
            },
        )
        db.commit()

    save_confirmed_answer(
        db,
        artifact_id,
        body.assertion_id,
        choice_index=body.choice_index,
        correct=bool(result.get("correct")),
    )
    record_answer(db, artifact_id, body.assertion_id)

    return result
