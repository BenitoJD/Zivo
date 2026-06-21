"""MCQ assertions — list, generate, flag, remediate."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, User
from app.repositories.intel import create_activity
from app.services.auth import get_current_user, require_csrf
from app.services.jobs import enqueue_generate

router = APIRouter()


class GenerateIn(BaseModel):
    artifact_id: uuid.UUID
    mode: str = "cover_concepts"
    pool_size: int = Field(default=5, ge=3, le=10)
    questions_per_page: int | None = None
    total_budget: int | None = None


class FlagIn(BaseModel):
    reason: str | None = None


@router.get("/{assertion_id}")
def get_assertion(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    row = db.execute(
        text(
            """
            SELECT id, title, summary, payload, status
            FROM intel.assertion
            WHERE id = :id
            """
        ),
        {"id": assertion_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


@router.get("")
def list_assertions(
    artifact_id: uuid.UUID,
    page: int | None = None,
    concept_key: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
  rows = db.execute(
      text(
          """
          SELECT id, title, summary, payload, status
          FROM intel.assertion
          WHERE payload->>'artifact_id' = :artifact_id
          ORDER BY recorded_at DESC
          LIMIT 200
          """
      ),
      {"artifact_id": str(artifact_id)},
  ).mappings().all()
  return [dict(r) for r in rows]


@router.post("/generate")
def generate_assertions(
    body: GenerateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(require_csrf),
) -> dict:
    activity_id = create_activity(
        db,
        type_uri="/vocab/activity/generate_questions",
        agent="api.assertions.generate",
        source_slug="user-upload",
        stats={"mode": body.mode, "pool_size": body.pool_size, "artifact_id": str(body.artifact_id)},
    )
    job = enqueue_generate(
        db,
        document_id=body.artifact_id,
        account_id=user.id,
        activity_id=activity_id,
        options=body.model_dump(),
    )
    db.commit()
    return {"activity_id": str(activity_id), "job_id": str(job.id)}


@router.post("/{assertion_id}/flag")
def flag_assertion(
    assertion_id: uuid.UUID,
    body: FlagIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(require_csrf),
) -> dict:
    db.execute(
        text(
            """
            INSERT INTO qb.question_feedback (assertion_id, account_id, reason)
            VALUES (:aid, :uid, :reason)
            """
        ),
        {"aid": assertion_id, "uid": user.id, "reason": body.reason},
    )
    db.commit()
    return {"ok": True}


@router.post("/{assertion_id}/remediate")
def remediate(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(require_csrf),
) -> dict:
    job = enqueue_generate(
        db,
        document_id=assertion_id,
        account_id=user.id,
        activity_id=create_activity(
            db,
            type_uri="/vocab/activity/generate_questions",
            agent="api.remediate",
            stats={"remediate_from": str(assertion_id)},
        ),
        options={"mode": "remediate", "source_assertion_id": str(assertion_id)},
    )
    db.commit()
    return {"job_id": str(job.id)}
