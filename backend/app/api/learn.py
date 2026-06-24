"""Server-authoritative Learn mode queue."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import Account, Document
from app.repositories import workspace as workspace_repo
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest import can_access_document
from app.services.guest_session import guest_session_for_read
from app.services.question_pool import (
    advance_to_next_page,
    build_learn_queue_state,
    ensure_question_pool,
    get_progress,
    is_page_complete,
    page_range_bounds,
)

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


def _learn_queue_payload(
    db: Session,
    artifact_id: uuid.UUID,
    doc: Document,
    user: Account | None,
) -> dict:
    progress = get_progress(doc)
    if is_page_complete(db, doc, progress):
        page = int(progress.get("current_page") or 1)
        _, page_to = page_range_bounds(doc)
        if page < page_to:
            advance_to_next_page(db, doc)
            db.refresh(doc)
            progress = get_progress(doc)
            ensure_question_pool(db, artifact_id)
            db.refresh(doc)
            progress = get_progress(doc)

    state = build_learn_queue_state(db, artifact_id, doc, progress)
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

    return {
        **state,
        "concepts": [dict(c) for c in concepts],
        "page_mastered": state["page_complete"],
        "page_ready": ws.get("status") == "page_ready",
        "can_advance": state["page_complete"] and not ws.get("status") == "page_ready",
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

    if doc.status == "ready":
        ensure_question_pool(db, artifact_id)
        db.refresh(doc)

    return _learn_queue_payload(db, artifact_id, doc, user)


@router.get("/{artifact_id}/learn-queue/stream")
async def learn_queue_stream(
    artifact_id: uuid.UUID,
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    db = SessionLocal()
    try:
        doc = db.get(Document, artifact_id)
        if not doc or not can_access_document(doc, user, guest_id):
            raise HTTPException(status_code=404, detail="Not found")
    finally:
        db.close()

    async def gen():
        delay = 1.0
        for _ in range(600):
            db = SessionLocal()
            try:
                doc = db.get(Document, artifact_id)
                if not doc or not can_access_document(doc, user, guest_id):
                    yield {"event": "error", "data": json.dumps({"detail": "not found"})}
                    return
                if doc.status == "ready":
                    ensure_question_pool(db, artifact_id)
                    db.refresh(doc)
                payload = _learn_queue_payload(db, artifact_id, doc, user)
                yield {"event": "queue", "data": json.dumps(payload, default=str)}
                if payload.get("current_assertion_id") and not payload.get("generation_pending"):
                    delay = 2.0
                elif payload.get("generation_pending") and (payload.get("questions_generated") or 0) == 0:
                    delay = min(8.0, delay * 1.4)
                else:
                    delay = 1.0
                if payload.get("document_complete"):
                    yield {"event": "done", "data": json.dumps(payload, default=str)}
                    return
            finally:
                db.close()
            await asyncio.sleep(delay)

    return EventSourceResponse(gen())


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
    progress = get_progress(doc)
    if int(progress.get("current_page") or 0) == page and is_page_complete(db, doc, progress):
        advance_to_next_page(db, doc)
        ensure_question_pool(db, artifact_id)
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
