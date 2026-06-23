"""MCQ grading — fast path + measurement persistence."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.graphs.mcq_graph import grade_mcq
from app.models import Account
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.question_pool import record_answer, save_confirmed_answer

router = APIRouter()


class GradeIn(BaseModel):
    assertion_id: uuid.UUID
    choice_index: int
    mode: str = "learn"
    latency_ms: int | None = None
    confidence: int | None = None


class AckIn(BaseModel):
    assertion_id: uuid.UUID


@router.post("/acknowledge", dependencies=[Depends(require_csrf_or_guest)])
def acknowledge(
    body: AckIn,
    db: Session = Depends(get_db),
) -> dict:
    """Mark a question answered so learn-queue advances (idempotent)."""
    row = db.execute(
        text("SELECT payload->>'artifact_id' AS artifact_id FROM intel.assertion WHERE id = :id"),
        {"id": body.assertion_id},
    ).first()
    if row and row[0]:
        record_answer(db, uuid.UUID(str(row[0])), body.assertion_id)
    return {"ok": True}


@router.post("/grade", dependencies=[Depends(require_csrf_or_guest)])
def grade(
    body: GradeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
) -> dict:
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

    row = db.execute(
        text("SELECT payload->>'artifact_id' AS artifact_id FROM intel.assertion WHERE id = :id"),
        {"id": body.assertion_id},
    ).first()
    if row and row[0]:
        artifact_id = uuid.UUID(str(row[0]))
        save_confirmed_answer(
            db,
            artifact_id,
            body.assertion_id,
            choice_index=body.choice_index,
            correct=bool(result.get("correct")),
        )
        record_answer(db, artifact_id, body.assertion_id)

    return result
