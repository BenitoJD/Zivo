"""Persist generate.questions progress on the job row for crash-safe resume."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.eta import context as eta_context
from app.models import Job


def _checkpoint_from_result(result: object) -> dict[str, Any]:
    return pick(
        not isinstance(result, dict),
        lambda: {},
        lambda: pick(
            isinstance(result.get("checkpoint"), dict),
            lambda: dict(result.get("checkpoint")),
            lambda: {},
        ),
    )


def load_generation_checkpoint(db: Session, job_id: uuid.UUID) -> dict[str, Any]:
    row = db.get(Job, job_id)
    return pick(not row, lambda: {}, lambda: _checkpoint_from_result(row.result))


def save_generation_checkpoint(
    db: Session,
    job_id: uuid.UUID,
    *,
    page_number: int,
    last_sequence: int,
    saved_total: int | None = None,
) -> None:
    row = db.get(Job, job_id)

    def _save() -> None:
        checkpoint: dict[str, Any] = {
            "page_number": int(page_number),
            "last_sequence": int(last_sequence),
        }
        pick(
            saved_total is not None,
            lambda: checkpoint.__setitem__("saved_total", int(saved_total)),
            lambda: None,
        )
        result = dict(row.result or {})
        result["checkpoint"] = checkpoint
        row.result = result
        db.commit()

    pick(not row, lambda: None, _save)


def resolve_start_sequence(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    options: dict[str, Any],
    job_id: uuid.UUID | None = None,
) -> int:
    """Resume after reclaim — max(payload, assertions on page, job checkpoint)."""
    from app.services.question_pool import count_assertions_on_page

    start = int(options.get("start_sequence") or 0)
    cook_mode = str(options.get("cook_mode") or "learn")
    from app.services.question_budget import parse_budget_mode

    serve_mode = parse_budget_mode(cook_mode)
    start = max(
        start,
        count_assertions_on_page(db, document_id, page_number, serve_mode=serve_mode),
    )

    jid = job_id or eta_context.get_current_job_id()

    def _from_job() -> int:
        checkpoint = load_generation_checkpoint(db, jid)
        return pick(
            int(checkpoint.get("page_number") or 0) == page_number,
            lambda: max(start, int(checkpoint.get("last_sequence") or 0)),
            lambda: start,
        )

    return pick(jid is not None, _from_job, lambda: start)


def checkpoint_after_save(
    db: Session,
    *,
    page_number: int,
    sequence: int,
    saved_total: int,
) -> None:
    job_id = eta_context.get_current_job_id()
    pick(
        job_id is None,
        lambda: None,
        lambda: save_generation_checkpoint(
            db,
            job_id,
            page_number=page_number,
            last_sequence=sequence,
            saved_total=saved_total,
        ),
    )
