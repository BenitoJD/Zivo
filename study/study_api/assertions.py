"""MCQ assertions — list, generate, flag, remediate."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.engine_runtime import apply, pick
from app.models import Account, User
from app.repositories.intel import create_activity
from app.services.auth import get_current_user, get_optional_user, require_csrf, require_csrf_or_guest
from app.services.document_access import require_document
from app.services.guest_session import guest_session_for_read
from app.services.http_outcome import evaluate_http_outcome
from app.services.jobs import enqueue_generate
from app.services.mcq_dedup import (
    coerce_mcq_options,
    sanitize_mcq_stem,
)
from app.services.presence import evaluate_presence
from app.services.rate_limit import rate_limit_dependency

router = APIRouter()


def _raise(exc: BaseException) -> None:
    raise exc


def _http(action: str, detail: str) -> None:
    _raise(HTTPException(status_code=evaluate_http_outcome(action).status, detail=detail))


def _sanitize_assertion_payload(payload: dict | None) -> dict:
    def _clean() -> dict:
        out = dict(payload)
        question = sanitize_mcq_stem(str(out.get("question") or out.get("stem") or ""))
        pick(
            bool(question),
            lambda: (out.__setitem__("question", question), out.pop("stem", None)),
            lambda: None,
        )
        raw_options = out.get("options") or out.get("choices")
        options = coerce_mcq_options(raw_options)
        pick(
            bool(options),
            lambda: (out.__setitem__("options", options), out.pop("choices", None)),
            lambda: None,
        )
        # Answer key must not leak to learners before they grade. The grade
        # endpoint (/api/mcq/grade) is the only place that returns the verdict,
        # correct index/indices, explanation, and per-option feedback. Here we
        # keep only a boolean is_multi so the UI can render select-all mode
        # without revealing how many answers are correct or which they are.
        multi_list = out.get("correct_indices")
        multi_list = pick(
            isinstance(multi_list, list),
            lambda: multi_list,
            lambda: pick(
                isinstance(out.get("correct_index"), list),
                lambda: out["correct_index"],
                lambda: None,
            ),
        )
        out["is_multi"] = bool(multi_list) and len(multi_list) >= 2
        for leaked in (
            "correct_index", "correct_indices", "explanation", "option_feedback",
            "answer", "answers", "quality",
        ):
            out.pop(leaked, None)
        return out

    return pick(isinstance(payload, dict), _clean, lambda: {})


def _sanitize_assertion_row(row: dict) -> dict:
    out = dict(row)
    payload = out.get("payload")
    pick(
        isinstance(payload, dict),
        lambda: out.__setitem__("payload", _sanitize_assertion_payload(payload)),
        lambda: None,
    )
    return out


def _assertion_access(
    db: Session,
    assertion_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
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
    apply(
        evaluate_presence(row).action,
        {
            "missing": lambda: _http("missing", "Not found"),
            "empty": lambda: _http("missing", "Not found"),
            "ok": lambda: None,
        },
    )
    artifact_raw = (row.get("payload") or {}).get("artifact_id")
    apply(
        evaluate_presence(artifact_raw).action,
        {
            "missing": lambda: _http("missing", "Not found"),
            "empty": lambda: _http("missing", "Not found"),
            "ok": lambda: None,
        },
    )
    require_document(db, uuid.UUID(str(artifact_raw)), user, guest_id)
    return _sanitize_assertion_row(dict(row))


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
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    return _assertion_access(db, assertion_id, user, guest_id)


@router.get("")
def list_assertions(
    artifact_id: uuid.UUID,
    page: int | None = None,
    concept_key: str | None = None,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> list[dict]:
    require_document(db, artifact_id, user, guest_id)
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
    return [_sanitize_assertion_row(dict(r)) for r in rows]


@router.post("/generate", dependencies=[Depends(require_csrf), Depends(rate_limit_dependency)])
def generate_assertions(
    body: GenerateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    require_document(db, body.artifact_id, user, guest_id)
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


@router.post("/{assertion_id}/flag", dependencies=[Depends(rate_limit_dependency), Depends(require_csrf_or_guest)])
def flag_assertion(
    assertion_id: uuid.UUID,
    body: FlagIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    _assertion_access(db, assertion_id, user, guest_id)
    db.execute(
        text(
            """
            INSERT INTO qb.question_feedback (assertion_id, account_id, reason)
            VALUES (:aid, :uid, :reason)
            """
        ),
        {
            "aid": assertion_id,
            "uid": pick(bool(user), lambda: user.id, lambda: None),
            "reason": body.reason,
        },
    )
    db.commit()
    return {"ok": True}


@router.post("/{assertion_id}/remediate", dependencies=[Depends(rate_limit_dependency)])
def remediate(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    guest_id: str | None = Depends(guest_session_for_read),
    _: None = Depends(require_csrf),
) -> dict:
    row = _assertion_access(db, assertion_id, user, guest_id)
    artifact_raw = (row.get("payload") or {}).get("artifact_id")
    apply(
        evaluate_presence(artifact_raw).action,
        {
            "missing": lambda: _http("missing", "Not found"),
            "empty": lambda: _http("missing", "Not found"),
            "ok": lambda: None,
        },
    )
    artifact_id = uuid.UUID(str(artifact_raw))
    job = enqueue_generate(
        db,
        document_id=artifact_id,
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
