from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.eta.registry import get_handler, normalize_payload
from app.models import Job, JobPriority, JobStatus, JobWorkload


def _merge_parent_job_ids(
    payload: dict[str, Any], parent_job_ids: Sequence[str] | None
) -> dict[str, Any]:
    if not parent_job_ids:
        return payload
    merged = dict(payload)
    existing = merged.get("parent_job_ids") or []
    if existing and not isinstance(existing, list):
        raise ValueError("payload.parent_job_ids must be a list when present")
    merged["parent_job_ids"] = sorted({str(job_id) for job_id in [*existing, *parent_job_ids]})
    return merged


def build_job(
    *,
    name: str,
    payload: Any,
    account_id: Any = None,
    workload: JobWorkload | None = None,
    priority: JobPriority | None = None,
    run_after: datetime | None = None,
    max_attempts: int | None = None,
    execution_id: object | None = None,
    node_key: str | None = None,
    parent_job_ids: Sequence[str] | None = None,
) -> Job:
    handler = get_handler(name)
    normalized_payload = normalize_payload(handler, payload)
    if not isinstance(normalized_payload, dict):
        raise ValueError("ETA job payload must normalize to a JSON object")
    normalized_payload = _merge_parent_job_ids(normalized_payload, parent_job_ids)

    resolved_max_attempts = max_attempts or 3
    if resolved_max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    job = Job(
        name=name,
        status=JobStatus.queued,
        workload=workload or (handler.workload if handler else JobWorkload.io),
        priority=priority or (handler.priority if handler else JobPriority.MEDIUM),
        account_id=account_id,
        execution_id=execution_id,
        node_key=node_key,
        payload=normalized_payload,
        run_after=run_after,
        max_attempts=resolved_max_attempts,
    )
    return job


@contextmanager
def eta_session(db: Session | None = None):
    if db is not None:
        yield db
        return

    owned_db = SessionLocal()
    try:
        yield owned_db
    finally:
        owned_db.close()


def submit_job(
    db: Session | None = None,
    *,
    name: str,
    payload: Any,
    account_id: Any = None,
    workload: JobWorkload | None = None,
    priority: JobPriority | None = None,
    run_after: datetime | None = None,
    max_attempts: int | None = None,
    execution_id: object | None = None,
    node_key: str | None = None,
    parent_job_ids: Sequence[str] | None = None,
    commit: bool = True,
) -> Job:
    with eta_session(db) as active_db:
        job = build_job(
            name=name,
            payload=payload,
            account_id=account_id,
            workload=workload,
            priority=priority,
            run_after=run_after,
            max_attempts=max_attempts,
            execution_id=execution_id,
            node_key=node_key,
            parent_job_ids=parent_job_ids,
        )
        active_db.add(job)
        if commit:
            active_db.commit()
            active_db.refresh(job)
        else:
            active_db.flush()
        return job
