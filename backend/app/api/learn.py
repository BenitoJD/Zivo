"""Server-authoritative Learn mode queue."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, Document, User
from app.repositories import workspace as workspace_repo
from app.services.auth import get_current_user
from app.services.guest import can_access_document

router = APIRouter()


class AdvanceIn(BaseModel):
    page: int


@router.get("/{artifact_id}/learn-queue")
def learn_queue(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, None):
        raise HTTPException(status_code=404, detail="Not found")
    captured = doc.artifact_captured_at or doc.created_at
    ws = workspace_repo.get_workspace(db, user.id, artifact_id, captured) or {}
    concepts = db.execute(
        text(
            """
            SELECT DISTINCT payload->>'primary_concept_key' AS concept_key,
                   payload->>'primary_concept' AS label
            FROM intel.assertion
            WHERE payload->>'artifact_id' = :aid
              AND payload->>'primary_concept_key' IS NOT NULL
            """
        ),
        {"aid": str(artifact_id)},
    ).mappings().all()
    next_row = db.execute(
        text(
            """
            SELECT id FROM intel.assertion
            WHERE payload->>'artifact_id' = :aid AND status = 'active'
            ORDER BY recorded_at ASC
            LIMIT 1
            """
        ),
        {"aid": str(artifact_id)},
    ).first()
    page_mastered = False
    page_ready = ws.get("status") == "page_ready"
    return {
        "current_page": ws.get("current_page") or (doc.meta or {}).get("selected_range", {}).get("from"),
        "current_assertion_id": str(next_row[0]) if next_row else None,
        "concepts": [dict(c) for c in concepts],
        "page_mastered": page_mastered,
        "page_ready": page_ready,
        "can_advance": page_mastered and not page_ready,
    }


@router.get("/{artifact_id}/mastery")
def mastery(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    q = learn_queue(artifact_id, db, user)
    return {
        "page": q["current_page"],
        "concepts": q["concepts"],
        "page_mastered": q["page_mastered"],
        "page_ready": q["page_ready"],
    }


@router.post("/{artifact_id}/pages/{page}/advance")
def advance_page(
    artifact_id: uuid.UUID,
    page: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    doc = db.get(Document, artifact_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    captured = doc.artifact_captured_at or doc.created_at
    workspace_repo.upsert_workspace(
        db,
        account_id=user.id,
        artifact_id=artifact_id,
        artifact_captured_at=captured,
        status="page_ready",
        unlocked_through_page=page,
    )
    db.commit()
    return {"unlocked_through_page": page, "page_ready": True}
