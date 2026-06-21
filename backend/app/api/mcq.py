"""MCQ grading — fast path + measurement persistence."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.graphs.mcq_graph import grade_mcq
from app.models import Account, User
from app.services.auth import get_optional_user, require_csrf_or_guest

router = APIRouter()


class GradeIn(BaseModel):
    assertion_id: uuid.UUID
    choice_index: int
    latency_ms: int | None = None
    confidence: int | None = None


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
    return result
