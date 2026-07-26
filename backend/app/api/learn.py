"""Server-authoritative Learn mode queue."""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import Account, Document
from app.repositories import workspace as workspace_repo
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.api.access import require_document
from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI, resolve_subject_entity
from app.services.guest_session import guest_session_for_read
from app.services.mcq_dedup import short_concept_label
from app.services.question_pool import (
    advance_to_next_page,
    build_learn_queue_state,
    ensure_question_pool,
    get_progress,
    get_study_mode,
    is_page_complete,
    learner_key_for,
    page_range_bounds,
    selected_page_list,
    set_focus_concept,
    set_serve_budget_mode,
    set_study_mode,
)
from app.services.question_budget import parse_budget_mode
from app.services.learn_notify import wait_learn_notify

router = APIRouter()

_CONCEPTS_CACHE_TTL_SECONDS = 30.0
_concepts_cache: dict[str, tuple[float, list[dict]]] = {}


def _artifact_concepts(db: Session, artifact_id: uuid.UUID) -> list[dict]:
    key = str(artifact_id)
    now = time.monotonic()
    cached = _concepts_cache.get(key)
    if cached and now - cached[0] < _CONCEPTS_CACHE_TTL_SECONDS:
        return cached[1]

    concepts = db.execute(
        text(
            """
            SELECT DISTINCT payload->>'primary_concept_key' AS concept_key,
                   payload->>'primary_concept' AS label
            FROM intel.assertion
            WHERE payload->>'artifact_id' = :aid
              AND status = 'active'
              AND payload->>'primary_concept_key' IS NOT NULL
            """
        ),
        {"aid": key},
    ).mappings().all()
    rows = [dict(c) for c in concepts]
    _concepts_cache[key] = (now, rows)
    return rows


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
    *,
    mode: str | None = None,
    guest_id: str | None = None,
) -> dict:
    lk = learner_key_for(user, guest_id)
    serve_mode = parse_budget_mode(mode)
    set_serve_budget_mode(db, doc, serve_mode, learner_key=lk)
    db.refresh(doc)
    progress = get_progress(doc, learner_key=lk)
    from app.services.newspaper import is_newspaper_document

    if not is_newspaper_document(doc) and is_page_complete(db, doc, progress):
        page = int(progress.get("current_page") or 1)
        _, page_to = page_range_bounds(doc)
        if page < page_to:
            advance_to_next_page(db, doc)
            db.refresh(doc)
            progress = get_progress(doc, learner_key=lk)
            ensure_question_pool(db, artifact_id)
            db.refresh(doc)
            progress = get_progress(doc, learner_key=lk)

    state = build_learn_queue_state(
        db, artifact_id, doc, progress, mode=serve_mode, learner_key=lk
    )
    ws = _workspace_state(db, doc, user)

    return {
        **state,
        "concepts": _artifact_concepts(db, artifact_id),
        "page_mastered": state["page_complete"],
        "page_ready": ws.get("status") == "page_ready",
        "can_advance": state["page_complete"] and not ws.get("status") == "page_ready",
    }


def _learn_queue_handler(
    artifact_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
    *,
    mode: str | None = None,
) -> dict:
    with SessionLocal() as db:
        doc = require_document(db, artifact_id, user, guest_id)
        if doc.status == "ready":
            ensure_question_pool(db, artifact_id)
            db.refresh(doc)
        return _learn_queue_payload(db, artifact_id, doc, user, mode=mode, guest_id=guest_id)


def _learn_stream_tick(
    artifact_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
    *,
    ensure_pool: bool,
    mode: str | None = None,
) -> dict:
    with SessionLocal() as db:
        doc = require_document(db, artifact_id, user, guest_id)
        if ensure_pool and doc.status == "ready":
            ensure_question_pool(db, artifact_id)
            db.refresh(doc)
        return _learn_queue_payload(db, artifact_id, doc, user, mode=mode, guest_id=guest_id)


@router.get("/{artifact_id}/learn-queue")
async def learn_queue(
    artifact_id: uuid.UUID,
    mode: str | None = None,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    db.close()
    return await asyncio.to_thread(
        _learn_queue_handler, artifact_id, user, guest_id, mode=mode
    )


@router.get("/{artifact_id}/learn-queue/stream")
async def learn_queue_stream(
    artifact_id: uuid.UUID,
    mode: str | None = None,
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    db = SessionLocal()
    try:
        require_document(db, artifact_id, user, guest_id)
    finally:
        db.close()

    async def gen():
        delay = 2.0
        tick = 0
        max_ticks = 180
        while tick < max_ticks:
            tick += 1
            try:
                payload = await asyncio.to_thread(
                    _learn_stream_tick,
                    artifact_id,
                    user,
                    guest_id,
                    ensure_pool=tick == 1,
                    mode=mode,
                )
            except HTTPException:
                yield {"event": "error", "data": json.dumps({"detail": "not found"})}
                return
            yield {"event": "queue", "data": json.dumps(payload, default=str)}
            if payload.get("document_complete"):
                yield {"event": "done", "data": json.dumps(payload, default=str)}
                return
            if payload.get("current_assertion_id") and not payload.get("generation_pending"):
                delay = 3.0
            elif payload.get("generation_pending"):
                delay = min(8.0, delay * 1.15)
            else:
                delay = 2.0
            woke = await asyncio.to_thread(wait_learn_notify, artifact_id, timeout=delay)
            if not woke:
                continue

    return EventSourceResponse(
        gen(),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform"},
    )


@router.get("/{artifact_id}/mastery")
async def mastery(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    db.close()
    q = await asyncio.to_thread(_learn_queue_handler, artifact_id, user, guest_id)
    return {
        "page": q["current_page"],
        "concepts": q["concepts"],
        "page_mastered": q["page_mastered"],
        "page_ready": q["page_ready"],
    }


@router.get("/{artifact_id}/report")
def study_report(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Persistent study report: first-attempt accuracy per concept for this learner,
    scoped to the page range they just finished.

    Sourced from the immutable intel.measurement rows (one per learner+item, the
    FIRST answer — retries are idempotent no-ops), so it survives reloads and is a
    true record, unlike the in-session client history. Scoped to the currently
    selected pages so the card reflects the range just completed, not the whole doc.
    """
    doc = require_document(db, artifact_id, user, guest_id)
    subject_id = resolve_subject_entity(db, user, guest_id)
    pages = selected_page_list(doc)
    if subject_id is None or not pages:
        return {"total": 0, "correct": 0, "wrong": 0, "topics": []}
    rows = db.execute(
        text(
            """
            SELECT COALESCE(NULLIF(TRIM(a.payload->>'primary_concept'), ''), 'General') AS concept,
                   COUNT(*) AS total,
                   COALESCE(SUM(m.value_numeric), 0) AS correct
            FROM intel.measurement m
            JOIN intel.assertion a ON a.id = m.source_assertion_id
            WHERE m.subject_entity_id = :subject
              AND m.metric_concept_id = :metric
              AND a.payload->>'artifact_id' = :artifact_id
              AND (a.payload->>'page_number')::int = ANY(:pages)
            GROUP BY 1
            ORDER BY (COALESCE(SUM(m.value_numeric), 0)::float / NULLIF(COUNT(*), 0)) ASC,
                     COUNT(*) DESC
            """
        ),
        {
            "subject": subject_id,
            "metric": concept_id(db, ANSWER_CORRECT_METRIC_URI),
            "artifact_id": str(artifact_id),
            "pages": pages,
        },
    ).mappings().all()
    topics = [
        {
            "concept": short_concept_label(r["concept"]),
            "correct": int(r["correct"]),
            "total": int(r["total"]),
        }
        for r in rows
    ]
    total = sum(t["total"] for t in topics)
    correct = sum(t["correct"] for t in topics)
    return {"total": total, "correct": correct, "wrong": total - correct, "topics": topics}


@router.post("/{artifact_id}/pages/{page}/advance", dependencies=[Depends(require_csrf_or_guest)])
def advance_page(
    artifact_id: uuid.UUID,
    page: int,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    doc = require_document(db, artifact_id, user, guest_id)
    progress = get_progress(doc)
    advanced = False
    if int(progress.get("current_page") or 0) == page and is_page_complete(db, doc, progress):
        advance_to_next_page(db, doc)
        ensure_question_pool(db, artifact_id)
        advanced = True
    if user and advanced:
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
    return {"unlocked_through_page": page, "page_ready": advanced}


class StudyModeBody(BaseModel):
    mode: str  # "adaptive" | "classic"


@router.post("/{artifact_id}/study-mode", dependencies=[Depends(require_csrf_or_guest)])
def set_study_mode_endpoint(
    artifact_id: uuid.UUID,
    body: StudyModeBody,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Switch this document between the Adaptive tutor and Classic (fixed) order.
    Both run over the same question pool, so the change takes effect on the next
    question with no regeneration."""
    doc = require_document(db, artifact_id, user, guest_id)
    lk = learner_key_for(user, guest_id)
    mode = set_study_mode(db, doc, body.mode, learner_key=lk)
    db.commit()
    return {"study_mode": mode}


@router.get("/{artifact_id}/study-mode")
def get_study_mode_endpoint(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    doc = require_document(db, artifact_id, user, guest_id)
    lk = learner_key_for(user, guest_id)
    return {"study_mode": get_study_mode(doc, learner_key=lk)}


class FocusConceptBody(BaseModel):
    concept: str | None = None


@router.post("/{artifact_id}/focus-concept", dependencies=[Depends(require_csrf_or_guest)])
def set_focus_concept_endpoint(
    artifact_id: uuid.UUID,
    body: FocusConceptBody,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Progress → Learn: prefer unanswered questions on this concept until cleared."""
    doc = require_document(db, artifact_id, user, guest_id)
    lk = learner_key_for(user, guest_id)
    set_focus_concept(db, doc, body.concept, learner_key=lk)
    db.commit()
    return {"focus_concept": (body.concept or "").strip() or None}