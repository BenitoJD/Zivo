"""MCQ grading — fast path + measurement persistence."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.graphs.mcq_graph import grade_mcq
from app.models import Account
from app.services.answer_signal import record_answer_signal, resolve_subject_entity
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.api.access import require_document
from app.services.guest_session import guest_session_for_read
from app.services.question_pool import record_answer, save_confirmed_answer
from app.services.rate_limit import rate_limit_dependency

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

    # Record the answer event for calibration — idempotently (a retry/replay does not
    # double-count). Logged-in users resolve to their account entity; anonymous guests
    # to a stable per-guest entity, so their answers still feed the moat. Calibration
    # (Elo) runs only on a genuinely new event and only when enabled.
    subject_entity_id = resolve_subject_entity(db, user, guest_id)
    learner_ability: float | None = None
    item_difficulty: float | None = None
    if subject_entity_id is not None:
        signal = record_answer_signal(
            db,
            subject_entity_id=subject_entity_id,
            assertion_id=body.assertion_id,
            correct=bool(result.get("correct")),
            choice_index=body.choice_index,
            latency_ms=body.latency_ms,
            confidence=body.confidence,
            mode=body.mode,
            guest_id=guest_id if not user else None,
            calibrate=get_settings().calibration_enabled,
        )
        db.commit()
        if signal.inserted:
            learner_ability = signal.ability
            item_difficulty = signal.difficulty

    save_confirmed_answer(
        db,
        artifact_id,
        body.assertion_id,
        choice_index=body.choice_index,
        correct=bool(result.get("correct")),
        learner_ability=learner_ability,
        item_difficulty=item_difficulty,
    )
    record_answer(db, artifact_id, body.assertion_id)

    return result


