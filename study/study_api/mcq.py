"""MCQ grading — fast path + measurement persistence."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.graphs.mcq_graph import grade_feedback, grade_mcq, grade_verdict, load_grade_payload
from app.models import Account, Document
from app.services.answer_signal import record_answer_signal, resolve_subject_entity
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.auth_gate import evaluate_auth_gate
from app.services.document_access import require_document
from app.services.guest_session import guest_session_for_read
from app.services.http_outcome import evaluate_http_outcome
from app.services.offline_pack import get_pack, is_expired, verify_pack
from app.services.presence import evaluate_presence
from app.services.question_pool import (
    build_learn_queue_state,
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
    choice_indices: list[int] | None = None
    mode: str = "learn"
    latency_ms: int | None = None
    confidence: int | None = None


class AckIn(BaseModel):
    assertion_id: uuid.UUID


def _raise(exc: BaseException) -> None:
    raise exc


def _http(action: str, detail: str) -> None:
    _raise(HTTPException(status_code=evaluate_http_outcome(action).status, detail=detail))


def _account_id(user: Account | None):
    return pick(bool(user), lambda: user.id, lambda: None)


def _require_actor(user: Account | None, guest_id: str | None) -> None:
    apply(
        evaluate_auth_gate(account=user, allow_guest=bool(guest_id)).action,
        {
            "allow": lambda: None,
            "guest": lambda: None,
            "redirect": lambda: _http("redirect", "Authentication required"),
            "deny": lambda: _http("deny", "Authentication required"),
        },
    )


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
    apply(
        first_match(
            (
                Rule(when=(Pred("missing", "truthy"),), action="missing"),
                Rule(when=(), action="ok"),
            ),
            {"missing": not row or not row[0]},
        ).action,
        {
            "missing": lambda: _http("missing", "Not found"),
            "ok": lambda: None,
        },
    )
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
    lk = learner_key_for(user, guest_id)
    record_answer(db, artifact_id, body.assertion_id, learner_key=lk)
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
    captured: dict[str, Any] = {
        "learner_ability": None,
        "item_difficulty": None,
        "ability_se": None,
        "mastery_stop": None,
        "revisit_hours": None,
        "revisit_ease": None,
        "revisit_repetitions": None,
    }

    def _with_subject() -> None:
        doc = db.get(Document, artifact_id)
        progress = pick(bool(doc), lambda: get_progress(doc, learner_key=lk), lambda: {})
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
            guest_id=pick(not user, lambda: guest_id, lambda: None),
            calibrate=get_settings().calibration_enabled,
            prior_interval_hours=progress.get("revisit_due_hours"),
            prior_ease=pick(
                bool(prior_key),
                lambda: concept_ease.get(prior_key),
                lambda: progress.get("revisit_ease"),
            ),
            prior_repetitions=pick(
                bool(prior_key),
                lambda: concept_reps.get(prior_key),
                lambda: progress.get("revisit_repetitions"),
            ),
        )
        db.commit()

        def _capture() -> None:
            captured["learner_ability"] = signal.ability
            captured["item_difficulty"] = signal.difficulty
            captured["ability_se"] = signal.ability_se
            captured["mastery_stop"] = signal.mastery_stop
            captured["revisit_hours"] = signal.revisit_hours
            captured["revisit_ease"] = signal.revisit_ease
            captured["revisit_repetitions"] = signal.revisit_repetitions

        pick(signal.inserted, _capture, lambda: None)

    pick(subject_entity_id is not None, _with_subject, lambda: None)

    save_confirmed_answer(
        db,
        artifact_id,
        body.assertion_id,
        choice_index=body.choice_index,
        correct=correct,
        learner_ability=captured["learner_ability"],
        item_difficulty=captured["item_difficulty"],
        ability_se=captured["ability_se"],
        mastery_stop=captured["mastery_stop"],
        revisit_hours=captured["revisit_hours"],
        revisit_ease=captured["revisit_ease"],
        revisit_repetitions=captured["revisit_repetitions"],
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
        stream_db = SessionLocal()
        try:
            payload = load_grade_payload(stream_db, body.assertion_id)

            async def missing():
                yield _sse("error", {"message": "Question not found"})

            async def found():
                verdict = grade_verdict(payload, body.choice_index, body.choice_indices)
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

                    def _next() -> str | None:
                        stream_db.refresh(doc)
                        lk = learner_key_for(user, guest_id)
                        progress = get_progress(doc, learner_key=lk)
                        answered = {str(x) for x in progress.get("answered_ids") or []}
                        answered.add(str(body.assertion_id))
                        progress = {**progress, "answered_ids": sorted(answered)}
                        return next_assertion_id(stream_db, artifact_id, progress)

                    next_id = pick(doc is not None, _next, lambda: None)
                except Exception:
                    logger.exception("grade answer-record failed")
                for ev in pick(
                    bool(next_id),
                    lambda: [_sse("next", {"next_assertion_id": next_id})],
                    lambda: [],
                ):
                    yield ev

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

            agen = pick(payload is None, missing, found)
            async for ev in agen:
                yield ev
        finally:
            stream_db.close()

    return EventSourceResponse(
        event_generator(),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform"},
    )


class GradeBatchIn(BaseModel):
    pack_id: uuid.UUID
    grades: list[GradeIn] = Field(min_length=1, max_length=2000)


@router.post(
    "/grade/batch",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def grade_batch(
    body: GradeBatchIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Replay a sequence of offline-graded answers through the real engines.

    The server recomputes correctness from the stored key for every grade
    (``grade_verdict``) — it never trusts a client-sent ``correct`` flag — then
    runs the same ``_record_graded_answer`` path as the live grade endpoint.
    Replay is safe-by-construction: the ``measurement_answer_idempotent`` partial
    unique index makes a duplicate (learner, assertion) grade a no-op for
    calibration. Returns the reconciled queue state so the client can refresh.
    """
    pick(
        not get_settings().offline_mode_enabled,
        lambda: _http("missing", "Not found"),
        lambda: None,
    )
    _require_actor(user, guest_id)
    pack = get_pack(
        db, body.pack_id, account_id=_account_id(user), guest_id=guest_id, include_payload=True
    )
    apply(
        evaluate_presence(pack).action,
        {
            "missing": lambda: _http("missing", "Not found"),
            "empty": lambda: _http("missing", "Not found"),
            "ok": lambda: None,
        },
    )
    pick(
        is_expired(pack),
        lambda: _raise(HTTPException(status_code=410, detail="Pack expired")),
        lambda: None,
    )
    payload = pack.get("pack_payload")
    payload = pick(
        isinstance(payload, dict),
        lambda: payload,
        lambda: pick(isinstance(payload, str), lambda: json.loads(payload), lambda: {}),
    )
    pick(
        not verify_pack(payload, str(pack.get("signature") or "")),
        lambda: _raise(HTTPException(status_code=400, detail="Pack signature mismatch")),
        lambda: None,
    )

    first_artifact = payload.get("document_id")
    apply(
        evaluate_presence(first_artifact).action,
        {
            "missing": lambda: _raise(HTTPException(status_code=400, detail="Malformed pack")),
            "empty": lambda: _raise(HTTPException(status_code=400, detail="Malformed pack")),
            "ok": lambda: None,
        },
    )
    artifact_id = uuid.UUID(str(first_artifact))
    require_document(db, artifact_id, user, guest_id)

    replayed = [0]

    def _replay_one(g: GradeIn, raw: dict, g_artifact: uuid.UUID) -> None:
        verdict = grade_verdict(raw, g.choice_index, g.choice_indices)
        _record_graded_answer(
            db, g, artifact_id=g_artifact,
            correct=bool(verdict["correct"]),
            user=user, guest_id=guest_id,
        )
        replayed[0] += 1

    for g in body.grades:
        g_artifact = _resolve_assertion_artifact(db, g.assertion_id, user, guest_id)
        raw = load_grade_payload(db, g.assertion_id)
        pick(raw is None, lambda: None, lambda: _replay_one(g, raw, g_artifact))

    def _mastery() -> dict[str, Any]:
        lk = learner_key_for(user, guest_id)
        progress = get_progress(doc, learner_key=lk)
        return build_learn_queue_state(db, artifact_id, doc, progress, learner_key=lk)

    doc = db.get(Document, artifact_id)
    mastery = pick(doc is not None, _mastery, lambda: {})
    return {"replayed": replayed[0], "mastery": mastery}
