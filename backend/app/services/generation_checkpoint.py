"""Persist generate.questions progress on the job row for crash-safe resume."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.eta import context as eta_context
from app.models import Job


def _checkpoint_from_result(result: object) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    checkpoint = result.get("checkpoint")
    return dict(checkpoint) if isinstance(checkpoint, dict) else {}


def load_generation_checkpoint(db: Session, job_id: uuid.UUID) -> dict[str, Any]:
    row = db.get(Job, job_id)
    if not row:
        return {}
    return _checkpoint_from_result(row.result)


def save_generation_checkpoint(
    db: Session,
    job_id: uuid.UUID,
    *,
    page_number: int,
    last_sequence: int,
    saved_total: int | None = None,
) -> None:
    row = db.get(Job, job_id)
    if not row:
        return

    checkpoint: dict[str, Any] = {
        "page_number": int(page_number),
        "last_sequence": int(last_sequence),
    }
    if saved_total is not None:
        checkpoint["saved_total"] = int(saved_total)

    result = dict(row.result or {})
    result["checkpoint"] = checkpoint
    row.result = result
    db.commit()


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
    start = max(start, count_assertions_on_page(db, document_id, page_number))

    jid = job_id or eta_context.get_current_job_id()
    if jid is not None:
        checkpoint = load_generation_checkpoint(db, jid)
        if int(checkpoint.get("page_number") or 0) == page_number:
            start = max(start, int(checkpoint.get("last_sequence") or 0))
    return start


def checkpoint_after_save(
    db: Session,
    *,
    page_number: int,
    sequence: int,
    saved_total: int,
) -> None:
    job_id = eta_context.get_current_job_id()
    if job_id is None:
        return
    save_generation_checkpoint(
        db,
        job_id,
        page_number=page_number,
        last_sequence=sequence,
        saved_total=saved_total,
    )
