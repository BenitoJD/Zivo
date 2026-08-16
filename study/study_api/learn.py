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
from app.engine_runtime import choose, pick
from app.models import Account, Document
from app.repositories import workspace as workspace_repo
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.document_access import require_document
from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI, resolve_subject_entity
from app.services.guest_session import guest_session_for_read
from app.services.rate_limit import rate_limit_dependency
from app.services.mcq_dedup import short_concept_label
from app.services.learn_answered_review import build_learn_answered_review
from app.services.question_pool import (
    advance_to_next_page,
    build_learn_queue_state,
    ensure_question_pool,
    get_progress,
    get_study_mode,
    is_page_complete,
    learner_key_for,
    newspaper_learn_pool_complete,
    page_range_bounds,
    selected_page_list,
    set_focus_concept,
    set_serve_budget_mode,
    set_study_mode,
)
from app.services.question_budget import parse_budget_mode
from app.services.learn_notify import wait_learn_notify
from app.services.session_design import (
    evaluate_advance_page,
    evaluate_learn_auto_advance,
    evaluate_test_downgrade,
    plan_learn_stream_delay,
)

router = APIRouter()

_CONCEPTS_CACHE_TTL_SECONDS = 30.0
_concepts_cache: dict[str, tuple[float, list[dict]]] = {}


def _artifact_concepts(db: Session, artifact_id: uuid.UUID) -> list[dict]:
    key = str(artifact_id)
    now = time.monotonic()
    cached = _concepts_cache.get(key)

    def _load() -> list[dict]:
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

    return pick(
        bool(cached) and now - cached[0] < _CONCEPTS_CACHE_TTL_SECONDS,
        lambda: cached[1],
        _load,
    )


def _workspace_state(
    db: Session,
    doc: Document,
    user: Account | None,
) -> dict:
    captured = doc.artifact_captured_at or doc.created_at

    def _for_user() -> dict:
        return workspace_repo.get_workspace(db, user.id, doc.id, captured) or {}

    def _for_guest() -> dict:
        meta = doc.meta or {}
        selected = meta.get("selected_range") or {}
        return {
            "current_page": selected.get("from"),
            "status": choose(doc.status == "ready", "page_ready", doc.status),
            "selected_range": selected,
        }

    return pick(bool(user), _for_user, _for_guest)


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
    from app.services.newspaper import is_newspaper_document

    requested = parse_budget_mode(mode)
    progress = get_progress(doc, learner_key=lk)
    learn_complete = pick(
        is_newspaper_document(doc) and requested == "test",
        lambda: newspaper_learn_pool_complete(db, artifact_id, doc, progress),
        lambda: True,
    )
    serve_mode = evaluate_test_downgrade(
        newspaper=is_newspaper_document(doc),
        serve_mode=requested,
        learn_complete=learn_complete,
    )
    set_serve_budget_mode(db, doc, serve_mode, learner_key=lk)
    db.refresh(doc)
    progress = get_progress(doc, learner_key=lk)

    def _advance() -> dict:
        advance_to_next_page(db, doc, learner_key=lk)
        db.refresh(doc)
        ensure_question_pool(db, artifact_id)
        db.refresh(doc)
        return get_progress(doc, learner_key=lk)

    page = int(progress.get("current_page") or 1)
    study_pages = selected_page_list(doc)
    last_study_page = pick(
        bool(study_pages),
        lambda: study_pages[-1],
        lambda: page_range_bounds(doc)[1],
    )
    progress = pick(
        evaluate_learn_auto_advance(
            page_complete=is_page_complete(db, doc, progress),
            page=page,
            last_page=last_study_page,
        ),
        _advance,
        lambda: progress,
    )

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


def _maybe_ensure_pool(db: Session, artifact_id: uuid.UUID, doc: Document, enabled: bool) -> None:
    pick(
        enabled and doc.status == "ready",
        lambda: (ensure_question_pool(db, artifact_id), db.refresh(doc)),
        lambda: None,
    )


def _learn_queue_handler(
    artifact_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
    *,
    mode: str | None = None,
) -> dict:
    with SessionLocal() as db:
        doc = require_document(db, artifact_id, user, guest_id)
        _maybe_ensure_pool(db, artifact_id, doc, True)
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
        _maybe_ensure_pool(db, artifact_id, doc, ensure_pool)
        return _learn_queue_payload(db, artifact_id, doc, user, mode=mode, guest_id=guest_id)


@router.get("/{artifact_id}/learn-queue", dependencies=[Depends(rate_limit_dependency)])
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


@router.get("/{artifact_id}/learn-answered")
async def learn_answered(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    doc = require_document(db, artifact_id, user, guest_id)
    items = build_learn_answered_review(db, artifact_id, doc, user, guest_id)
    return {"items": items}


@router.get("/{artifact_id}/learn-queue/stream", dependencies=[Depends(rate_limit_dependency)])
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
            plan = plan_learn_stream_delay(
                document_complete=bool(payload.get("document_complete")),
                current_assertion_id=payload.get("current_assertion_id"),
                generation_pending=bool(payload.get("generation_pending")),
                delay=delay,
            )
            delay = plan.delay
            done_events = pick(
                plan.action == "done",
                lambda: [{"event": "done", "data": json.dumps(payload, default=str)}],
                lambda: [],
            )
            for ev in done_events:
                yield ev

            async def _noop() -> None:
                return None

            await pick(
                plan.action == "done",
                _noop,
                lambda: asyncio.to_thread(wait_learn_notify, artifact_id, timeout=delay),
            )
            tick = pick(plan.action == "done", lambda: max_ticks, lambda: tick)

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

    def _build() -> dict:
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

    return pick(
        subject_id is None or not pages,
        lambda: {"total": 0, "correct": 0, "wrong": 0, "topics": []},
        _build,
    )


@router.post("/{artifact_id}/pages/{page}/advance", dependencies=[Depends(require_csrf_or_guest)])
def advance_page(
    artifact_id: uuid.UUID,
    page: int,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    doc = require_document(db, artifact_id, user, guest_id)
    lk = learner_key_for(user, guest_id)
    progress = get_progress(doc, learner_key=lk)
    advanced = evaluate_advance_page(
        current_page=int(progress.get("current_page") or 0),
        requested_page=page,
        page_complete=is_page_complete(db, doc, progress),
    )

    def _do_advance() -> None:
        advance_to_next_page(db, doc, learner_key=lk)
        ensure_question_pool(db, artifact_id)

    pick(advanced, _do_advance, lambda: None)

    def _workspace() -> None:
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

    pick(bool(user) and advanced, _workspace, lambda: None)
    return {"unlocked_through_page": page, "page_ready": advanced}


class StudyModeBody(BaseModel):
    mode: str


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
