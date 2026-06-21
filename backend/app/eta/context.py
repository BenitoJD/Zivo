import uuid
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Job

_CurrentJobId = ContextVar[uuid.UUID | None]("eta_current_job_id", default=None)


def _coerce_job_id(job_id: str | uuid.UUID) -> uuid.UUID:
    if isinstance(job_id, uuid.UUID):
        return job_id
    try:
        return uuid.UUID(str(job_id))
    except ValueError as exc:
        raise ValueError(f"Invalid job_id: {job_id}") from exc


def set_current_job_id(job_id: str | uuid.UUID | None) -> Token:
    if job_id is None:
        return _CurrentJobId.set(None)
    return _CurrentJobId.set(_coerce_job_id(job_id))


def reset_current_job_id(token: Token) -> None:
    _CurrentJobId.reset(token)


def get_current_job_id() -> uuid.UUID | None:
    return _CurrentJobId.get()


def _load_parent_results_for_job(db: Session, job_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    job = db.get(Job, job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    payload = job.payload or {}
    parent_job_ids = payload.get("parent_job_ids") or []
    if not isinstance(parent_job_ids, list):
        raise ValueError("Job payload field 'parent_job_ids' must be a list")

    parent_results: dict[str, dict[str, Any]] = {}
    for parent_job_id in parent_job_ids:
        parent_uuid = _coerce_job_id(parent_job_id)
        parent_job = db.get(Job, parent_uuid)
        parent_results[str(parent_uuid)] = {
            "status": parent_job.status if parent_job else None,
            "result": parent_job.result if parent_job else None,
            "error": parent_job.error if parent_job else "Parent job not found",
            "name": parent_job.name if parent_job else None,
        }

    return parent_results


def get_parent_results(
    job_id: str | uuid.UUID | None = None,
    db: Session | None = None,
) -> dict[str, dict[str, Any]]:
    resolved_job_id = _coerce_job_id(job_id) if job_id is not None else get_current_job_id()
    if resolved_job_id is None:
        raise ValueError("No job_id provided and no current ETA job context available")

    if db is not None:
        return _load_parent_results_for_job(db, resolved_job_id)

    local_db = SessionLocal()
    try:
        return _load_parent_results_for_job(local_db, resolved_job_id)
    finally:
        local_db.close()
