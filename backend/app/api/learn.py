"""Server-authoritative Learn mode queue."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, Document
from app.repositories import workspace as workspace_repo
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest import can_access_document
from app.services.guest_session import guest_session_for_read

router = APIRouter()


def _workspace_state(
    db: Session,
    doc: Document,
    user: Account | None,
) -> dict:
    captured = doc.artifact_captured_at or doc.created_at
    if user:
        return workspace_repo.get_workspace(db, user.id, doc.id, captured) or {}
    meta = doc.meta or {}
    selected = meta.get("selected_range") or {}
    return {
        "current_page": selected.get("from"),
        "status": "page_ready" if doc.status == "ready" else doc.status,
        "selected_range": selected,
    }


@router.get("/{artifact_id}/learn-queue")
def learn_queue(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Not found")
    ws = _workspace_state(db, doc, user)
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
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    q = learn_queue(artifact_id, db, user, guest_id)
    return {
        "page": q["current_page"],
        "concepts": q["concepts"],
        "page_mastered": q["page_mastered"],
        "page_ready": q["page_ready"],
    }


@router.post("/{artifact_id}/pages/{page}/advance", dependencies=[Depends(require_csrf_or_guest)])
def advance_page(
    artifact_id: uuid.UUID,
    page: int,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Not found")
    if user:
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
