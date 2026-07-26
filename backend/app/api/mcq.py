"""MCQ grading — fast path + measurement persistence."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.graphs.mcq_graph import grade_feedback, grade_mcq, grade_verdict, load_grade_payload
from app.models import Account, Document
from app.services.answer_signal import record_answer_signal, resolve_subject_entity
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.api.access import require_document
from app.services.guest_session import guest_session_for_read
from app.services.question_pool import (
    get_progress,
    learner_key_for,
    next_assertion_id,
    record_answer,
    save_confirmed_answer,
)
from app.services.rate_limit import rate_limit_dependency

logger = logging.getLogger(__name__)
router = APIRouter()


class GradeIn(BaseModel):
    assertion_id: uuid.UUID
    choice_index: int
    # Multi-select ("select all that apply") items send the full chosen set here;
    # single-best-answer items leave it null and use choice_index.
    choice_indices: list[int] | None = None
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
    require_document(db, artifact_id, user, guest_id)
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


def _record_graded_answer(
    db: Session,
    body: GradeIn,
    *,
    artifact_id: uuid.UUID,
    correct: bool,
    user: Account | None,
    guest_id: str | None,
) -> None:
    """Persist the answer event for calibration + review history (idempotent).

    Depends only on the verdict (``correct``), never on the coaching feedback — so
    it can run the instant the verdict is known, before any LLM work. A retry/replay
    does not double-count; Elo calibration runs only on a genuinely new event.
    """
    subject_entity_id = resolve_subject_entity(db, user, guest_id)
    lk = learner_key_for(user, guest_id)
    learner_ability: float | None = None
    item_difficulty: float | None = None
    ability_se: float | None = None
    mastery_stop: bool | None = None
    revisit_hours: float | None = None
    revisit_ease: float | None = None
    revisit_repetitions: int | None = None
    if subject_entity_id is not None:
        doc = db.get(Document, artifact_id)
        progress = get_progress(doc, learner_key=lk) if doc else {}
        concept_ease = dict(progress.get("concept_revisit_ease") or {})
        concept_reps = dict(progress.get("concept_revisit_repetitions") or {})
        prior_key = db.execute(
            text(
                "SELECT payload->>'primary_concept_key' FROM intel.assertion WHERE id = :id"
            ),
            {"id": body.assertion_id},
        ).scalar()
        prior_key = str(prior_key or "") or None
        signal = record_answer_signal(
            db,
            subject_entity_id=subject_entity_id,
            assertion_id=body.assertion_id,
            correct=correct,
            choice_index=body.choice_index,
            latency_ms=body.latency_ms,
            confidence=body.confidence,
            mode=body.mode,
            guest_id=guest_id if not user else None,
            calibrate=get_settings().calibration_enabled,
            prior_interval_hours=progress.get("revisit_due_hours"),
            prior_ease=concept_ease.get(prior_key) if prior_key else progress.get("revisit_ease"),
            prior_repetitions=(
                concept_reps.get(prior_key) if prior_key else progress.get("revisit_repetitions")
            ),
        )
        db.commit()
        if signal.inserted:
            learner_ability = signal.ability
            item_difficulty = signal.difficulty
            ability_se = signal.ability_se
            mastery_stop = signal.mastery_stop
            revisit_hours = signal.revisit_hours
            revisit_ease = signal.revisit_ease
            revisit_repetitions = signal.revisit_repetitions

    save_confirmed_answer(
        db,
        artifact_id,
        body.assertion_id,
        choice_index=body.choice_index,
        correct=correct,
        learner_ability=learner_ability,
        item_difficulty=item_difficulty,
        ability_se=ability_se,
        mastery_stop=mastery_stop,
        revisit_hours=revisit_hours,
        revisit_ease=revisit_ease,
        revisit_repetitions=revisit_repetitions,
        learner_key=lk,
    )
    record_answer(db, artifact_id, body.assertion_id, learner_key=lk)


@router.post("/grade", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
def grade(
    body: GradeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    _require_actor(user, guest_id)
    artifact_id = _resolve_assertion_artifact(db, body.assertion_id, user, guest_id)
    result = grade_mcq(db, body.assertion_id, body.choice_index, choice_indices=body.choice_indices)
    _record_graded_answer(
        db, body, artifact_id=artifact_id, correct=bool(result.get("correct")), user=user, guest_id=guest_id
    )
    return result


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data)}


@router.post(
    "/grade/stream",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def grade_stream(
    body: GradeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> EventSourceResponse:
    """Verdict-first grading: the outcome streams in ~instantly (a pure index
    compare + stored explanation), the LLM coaching follows a moment later.

    Access + existence are checked up front so a real 401/403/404 is returned
    before the 200 stream opens.
    """
    _require_actor(user, guest_id)
    artifact_id = _resolve_assertion_artifact(db, body.assertion_id, user, guest_id)

    async def event_generator() -> Any:
        # Request-scoped db can close before the generator finishes; use a dedicated
        # session for the verdict write + feedback resolution.
        stream_db = SessionLocal()
        try:
            payload = load_grade_payload(stream_db, body.assertion_id)
            if payload is None:
                yield _sse("error", {"message": "Question not found"})
                return

            verdict = grade_verdict(payload, body.choice_index, body.choice_indices)
            # Yield verdict FIRST — before calibration / progress writes — so the
            # learner sees the outcome immediately. Bookkeeping is idempotent and
            # runs after yield; a disconnect mid-stream still records on retry.
            yield _sse("verdict", verdict)

            next_id: str | None = None
            try:
                _record_graded_answer(
                    stream_db,
                    body,
                    artifact_id=artifact_id,
                    correct=bool(verdict["correct"]),
                    user=user,
                    guest_id=guest_id,
                )
                doc = stream_db.get(Document, artifact_id)
                if doc is not None:
                    stream_db.refresh(doc)
                    lk = learner_key_for(user, guest_id)
                    progress = get_progress(doc, learner_key=lk)
                    next_id = next_assertion_id(stream_db, artifact_id, progress)
            except Exception:
                logger.exception("grade answer-record failed")
            if next_id:
                yield _sse("next", {"next_assertion_id": next_id})

            try:
                feedback = await grade_feedback(
                    stream_db,
                    body.assertion_id,
                    payload,
                    choice_index=body.choice_index,
                    choice_indices=body.choice_indices,
                )
            except Exception:
                logger.exception("grade feedback failed")
                feedback = verdict.get("explanation") or ""
            yield _sse("feedback", {"feedback": feedback})
            yield _sse("done", {})
        finally:
            stream_db.close()

    return EventSourceResponse(
        event_generator(),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform"},
    )


