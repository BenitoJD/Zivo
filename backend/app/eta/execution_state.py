from datetime import datetime, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import EtaExecution, EtaExecutionStatus, Job, JobStatus

def _resolve_execution_status(counts: dict[JobStatus, int], total: int) -> EtaExecutionStatus:
    queued = counts.get(JobStatus.queued, 0)
    running = counts.get(JobStatus.running, 0)
    succeeded = counts.get(JobStatus.succeeded, 0)
    failed = counts.get(JobStatus.failed, 0)
    cancelled = counts.get(JobStatus.cancelled, 0)

    if total == 0:
        return EtaExecutionStatus.queued

    if running > 0 or queued > 0:
        return EtaExecutionStatus.running

    if succeeded == total:
        return EtaExecutionStatus.succeeded

    if cancelled == total:
        return EtaExecutionStatus.cancelled

    if failed > 0 and succeeded == 0 and cancelled == 0:
        return EtaExecutionStatus.failed

    return EtaExecutionStatus.partial_failed


def _status_counts_from_rows(rows) -> dict[JobStatus, int]:
    counts: dict[JobStatus, int] = {
        JobStatus.queued: 0,
        JobStatus.running: 0,
        JobStatus.succeeded: 0,
        JobStatus.failed: 0,
        JobStatus.cancelled: 0,
    }
    for status, count in rows:
        # Job.status is stored as a plain string; coerce back to the enum for lookup.
        key = status if isinstance(status, JobStatus) else JobStatus(status)
        counts[key] = count
    return counts


def update_execution_state_sync(db: Session, execution_id: object) -> None:
    locked_execution_id = db.execute(
        select(EtaExecution.id).where(EtaExecution.id == execution_id).with_for_update()
    ).scalar_one_or_none()
    if not locked_execution_id:
        return

    rows = db.execute(
        select(Job.status, func.count())
        .where(Job.execution_id == locked_execution_id)
        .group_by(Job.status)
    ).all()
    counts = _status_counts_from_rows(rows)
    total = sum(counts.values())
    status = _resolve_execution_status(counts, total)

    completed_at = (
        datetime.now(timezone.utc)
        if counts[JobStatus.queued] == 0 and counts[JobStatus.running] == 0
        else None
    )

    db.execute(
        update(EtaExecution)
        .where(EtaExecution.id == locked_execution_id)
        .values(
            status=status,
            total_jobs=total,
            queued_jobs=counts[JobStatus.queued],
            running_jobs=counts[JobStatus.running],
            succeeded_jobs=counts[JobStatus.succeeded],
            failed_jobs=counts[JobStatus.failed],
            cancelled_jobs=counts[JobStatus.cancelled],
            completed_at=completed_at,
            updated_at=func.now(),
        )
    )


def cancel_descendants_sync(db: Session, *, job_id: object, error_message: str) -> None:
    db.execute(
        text(
            """
            WITH RECURSIVE descendants AS (
                SELECT job_id
                FROM eta_job_dependencies
                WHERE depends_on_job_id = :job_id
                UNION
                SELECT d.job_id
                FROM eta_job_dependencies d
                JOIN descendants ds ON d.depends_on_job_id = ds.job_id
            )
            UPDATE jobs
            SET status = :cancelled,
                error = COALESCE(error, :error_message),
                locked_by = NULL,
                locked_at = NULL,
                updated_at = NOW()
            WHERE id IN (SELECT job_id FROM descendants)
              AND status = :queued
            """
        ),
        {
            "job_id": str(job_id),
            "cancelled": JobStatus.cancelled.value,
            "queued": JobStatus.queued.value,
            "error_message": error_message,
        },
    )


async def update_execution_state_async(session: AsyncSession, execution_id: object) -> None:
    locked_execution_id = (
        await session.execute(
            select(EtaExecution.id).where(EtaExecution.id == execution_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not locked_execution_id:
        return

    rows = (
        await session.execute(
            select(Job.status, func.count())
            .where(Job.execution_id == locked_execution_id)
            .group_by(Job.status)
        )
    ).all()
    counts = _status_counts_from_rows(rows)
    total = sum(counts.values())
    status = _resolve_execution_status(counts, total)

    completed_at = (
        datetime.now(timezone.utc)
        if counts[JobStatus.queued] == 0 and counts[JobStatus.running] == 0
        else None
    )

    await session.execute(
        update(EtaExecution)
        .where(EtaExecution.id == locked_execution_id)
        .values(
            status=status,
            total_jobs=total,
            queued_jobs=counts[JobStatus.queued],
            running_jobs=counts[JobStatus.running],
            succeeded_jobs=counts[JobStatus.succeeded],
            failed_jobs=counts[JobStatus.failed],
            cancelled_jobs=counts[JobStatus.cancelled],
            completed_at=completed_at,
            updated_at=func.now(),
        )
    )


async def cancel_descendants_async(
    session: AsyncSession, *, job_id: object, error_message: str
) -> None:
    await session.execute(
        text(
            """
            WITH RECURSIVE descendants AS (
                SELECT job_id
                FROM eta_job_dependencies
                WHERE depends_on_job_id = :job_id
                UNION
                SELECT d.job_id
                FROM eta_job_dependencies d
                JOIN descendants ds ON d.depends_on_job_id = ds.job_id
            )
            UPDATE jobs
            SET status = :cancelled,
                error = COALESCE(error, :error_message),
                locked_by = NULL,
                locked_at = NULL,
                updated_at = NOW()
            WHERE id IN (SELECT job_id FROM descendants)
              AND status = :queued
            """
        ),
        {
            "job_id": str(job_id),
            "cancelled": JobStatus.cancelled.value,
            "queued": JobStatus.queued.value,
            "error_message": error_message,
        },
    )
