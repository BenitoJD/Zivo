from datetime import datetime, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, first_match, pick
from app.models import EtaExecution, EtaExecutionStatus, Job, JobStatus

_STATUS_RULES = (
    Rule(when=(Pred("total", "eq", 0),), action="queued"),
    Rule(when=(Pred("in_flight", "truthy"),), action="running"),
    Rule(when=(Pred("all_succeeded", "truthy"),), action="succeeded"),
    Rule(when=(Pred("all_cancelled", "truthy"),), action="cancelled"),
    Rule(when=(Pred("all_failed", "truthy"),), action="failed"),
    Rule(when=(), action="partial_failed"),
)

_STATUS_MAP = {
    "queued": EtaExecutionStatus.queued,
    "running": EtaExecutionStatus.running,
    "succeeded": EtaExecutionStatus.succeeded,
    "cancelled": EtaExecutionStatus.cancelled,
    "failed": EtaExecutionStatus.failed,
    "partial_failed": EtaExecutionStatus.partial_failed,
}


def _resolve_execution_status(counts: dict[JobStatus, int], total: int) -> EtaExecutionStatus:
    queued = counts.get(JobStatus.queued, 0)
    running = counts.get(JobStatus.running, 0)
    succeeded = counts.get(JobStatus.succeeded, 0)
    failed = counts.get(JobStatus.failed, 0)
    cancelled = counts.get(JobStatus.cancelled, 0)
    hit = first_match(
        _STATUS_RULES,
        {
            "total": total,
            "in_flight": running > 0 or queued > 0,
            "all_succeeded": succeeded == total,
            "all_cancelled": cancelled == total,
            "all_failed": failed > 0 and succeeded == 0 and cancelled == 0,
        },
    )
    return _STATUS_MAP[hit.action]


def _status_counts_from_rows(rows) -> dict[JobStatus, int]:
    counts: dict[JobStatus, int] = {
        JobStatus.queued: 0,
        JobStatus.running: 0,
        JobStatus.succeeded: 0,
        JobStatus.failed: 0,
        JobStatus.cancelled: 0,
    }

    def _add(status, count) -> None:
        key = pick(
            isinstance(status, JobStatus),
            lambda: status,
            lambda: JobStatus(status),
        )
        counts[key] = count

    for status, count in rows:
        _add(status, count)
    return counts


def _completed_at(counts: dict[JobStatus, int]) -> datetime | None:
    done = counts[JobStatus.queued] == 0 and counts[JobStatus.running] == 0
    return pick(done, lambda: datetime.now(timezone.utc), lambda: None)


def _execution_values(status: EtaExecutionStatus, counts: dict[JobStatus, int], total: int) -> dict:
    return {
        "status": status,
        "total_jobs": total,
        "queued_jobs": counts[JobStatus.queued],
        "running_jobs": counts[JobStatus.running],
        "succeeded_jobs": counts[JobStatus.succeeded],
        "failed_jobs": counts[JobStatus.failed],
        "cancelled_jobs": counts[JobStatus.cancelled],
        "completed_at": _completed_at(counts),
        "updated_at": func.now(),
    }


def update_execution_state_sync(db: Session, execution_id: object) -> None:
    locked_execution_id = db.execute(
        select(EtaExecution.id).where(EtaExecution.id == execution_id).with_for_update()
    ).scalar_one_or_none()

    def _write() -> None:
        rows = db.execute(
            select(Job.status, func.count())
            .where(Job.execution_id == locked_execution_id)
            .group_by(Job.status)
        ).all()
        counts = _status_counts_from_rows(rows)
        total = sum(counts.values())
        status = _resolve_execution_status(counts, total)
        db.execute(
            update(EtaExecution)
            .where(EtaExecution.id == locked_execution_id)
            .values(**_execution_values(status, counts, total))
        )

    pick(not locked_execution_id, lambda: None, _write)


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

    async def _write() -> None:
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
        await session.execute(
            update(EtaExecution)
            .where(EtaExecution.id == locked_execution_id)
            .values(**_execution_values(status, counts, total))
        )

    async def _skip() -> None:
        return None

    await pick(not locked_execution_id, _skip, _write)


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
