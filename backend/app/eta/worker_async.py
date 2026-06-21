import asyncio
import inspect
import logging
import os
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.eta import context as eta_context
from app.eta.execution_state import cancel_descendants_async, update_execution_state_async
from app.eta.handlers import io as _io_handlers  # noqa: F401
from app.eta.priority import priority_sort_key
from app.eta.registry import get_handler, normalize_result
from app.models import EtaJobDependency, Job, JobStatus, JobWorkload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()

WORKER_ID = os.getenv("ETA_WORKER_ID", str(uuid.uuid4()))
POLL_INTERVAL = float(os.getenv("ETA_WORKER_POLL_INTERVAL", "2.0"))
RETRY_DELAY_SECONDS = float(os.getenv("ETA_WORKER_RETRY_DELAY_SECONDS", "5.0"))


def _parse_workloads(value: str) -> Sequence[JobWorkload]:
    workloads = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            workloads.append(JobWorkload(raw))
        except ValueError:
            continue
    return workloads or [JobWorkload.io]


WORKLOADS = _parse_workloads(os.getenv("ETA_WORKER_WORKLOADS", "io"))
CONCURRENCY = int(os.getenv("ETA_IO_CONCURRENCY", "16"))

# Jobs left in 'running' longer than this are considered orphaned (worker crash)
# and reclaimed back to 'queued' on startup. Configurable; 10 min default.
STALE_RUNNING_TIMEOUT_SECONDS = float(os.getenv("ETA_STALE_RUNNING_TIMEOUT", "600"))


async def _reclaim_stale_jobs(session: AsyncSession) -> int:
    """Reset orphaned 'running' jobs (from a crashed worker) back to 'queued'.

    Only reclaims jobs whose workload matches this worker's WORKLOADS.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS)
    workload_filter = [w.value for w in WORKLOADS]
    result = await session.execute(
        update(Job)
        .where(Job.status == JobStatus.running)
        .where(Job.workload.in_(workload_filter))
        .where(Job.locked_at.is_not(None))
        .where(Job.locked_at < cutoff)
        .values(
            status=JobStatus.queued,
            locked_by=None,
            locked_at=None,
            error=None,
            updated_at=func.now(),
        )
    )
    return result.rowcount or 0


def _async_url() -> str:
    url = settings.database_url
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


engine = create_async_engine(_async_url(), pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _reserve_next_job(session: AsyncSession) -> tuple[object, object] | None:
    parent_job = aliased(Job)
    unsatisfied_dependency = (
        select(EtaJobDependency.job_id)
        .join(parent_job, parent_job.id == EtaJobDependency.depends_on_job_id)
        .where(
            EtaJobDependency.job_id == Job.id,
            parent_job.status != JobStatus.succeeded,
        )
    )
    workload_filter = [w.value for w in WORKLOADS]
    subquery = (
        select(Job.id, Job.execution_id)
        .where(Job.status == JobStatus.queued)
        .where(Job.workload.in_(workload_filter))
        .where(or_(Job.run_after.is_(None), Job.run_after <= func.now()))
        .where(~unsatisfied_dependency.exists())
        .order_by(priority_sort_key().desc(), Job.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
        .subquery()
    )
    result = await session.execute(
        update(Job)
        .where(Job.id == subquery.c.id)
        .values(
            status=JobStatus.running,
            locked_by=WORKER_ID,
            locked_at=func.now(),
            attempts=Job.attempts + 1,
            updated_at=func.now(),
        )
        .returning(Job.id, Job.execution_id)
    )
    row = result.first()
    return (row[0], row[1]) if row else None


async def _load_job_snapshot(
    session: AsyncSession, job_id: str
) -> tuple[str, dict, int, int, object] | None:
    job = await session.get(Job, job_id)
    if not job:
        return None
    return job.name, job.payload, job.attempts, job.max_attempts, job.execution_id


async def _mark_succeeded(
    session: AsyncSession, job_id: str, result: dict, execution_id: object
) -> None:
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.succeeded,
            result=result,
            error=None,
            locked_by=None,
            locked_at=None,
            updated_at=func.now(),
        )
    )
    if execution_id:
        await update_execution_state_async(session, execution_id)


async def _mark_failed(
    session: AsyncSession,
    job_id: str,
    attempts: int,
    max_attempts: int,
    error: str,
    execution_id: object,
) -> None:
    if attempts >= max_attempts:
        status = JobStatus.failed
        run_after = None
    else:
        status = JobStatus.queued
        run_after = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_SECONDS)

    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=status,
            error=error,
            run_after=run_after,
            locked_by=None,
            locked_at=None,
            updated_at=func.now(),
        )
    )
    if status == JobStatus.failed:
        await cancel_descendants_async(
            session,
            job_id=job_id,
            error_message=f"Upstream dependency failed: {job_id}",
        )
    if execution_id:
        await update_execution_state_async(session, execution_id)


async def _process_job(job_id: object, job_name: str, payload: dict) -> dict:
    token = eta_context.set_current_job_id(job_id)
    handler = get_handler(job_name)
    try:
        if not handler:
            raise RuntimeError(f"Unknown job name: {job_name}")

        if handler.input_model:
            payload = handler.input_model.model_validate(payload)

        if inspect.iscoroutinefunction(handler.fn):
            result = await handler.fn(payload)
        else:
            result = await asyncio.to_thread(handler.fn, payload)

        return normalize_result(handler, result)
    finally:
        eta_context.reset_current_job_id(token)


async def _handle_job(job_id: object, execution_id: object, semaphore: asyncio.Semaphore) -> None:
    try:
        async with AsyncSessionLocal() as session:
            snapshot = await _load_job_snapshot(session, job_id)
        if not snapshot:
            return
        job_name, payload, attempts, max_attempts, execution_id = snapshot
        logger.info(
            "ETA async job started",
            extra={"job_id": str(job_id), "job_name": job_name, "attempt": attempts, "max_attempts": max_attempts},
        )
        try:
            result = await _process_job(job_id, job_name, payload)
            async with AsyncSessionLocal() as session, session.begin():
                await _mark_succeeded(session, job_id, result, execution_id)
            logger.info("ETA async job succeeded", extra={"job_id": str(job_id)})
        except Exception as exc:
            async with AsyncSessionLocal() as session, session.begin():
                await _mark_failed(session, job_id, attempts, max_attempts, str(exc), execution_id)
            logger.exception("ETA async job failed", extra={"job_id": str(job_id)})
    finally:
        semaphore.release()


async def run_eta_worker_async() -> None:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    # Reclaim jobs orphaned by a prior worker crash before entering the loop.
    try:
        async with AsyncSessionLocal() as session, session.begin():
            reclaimed = await _reclaim_stale_jobs(session)
        if reclaimed:
            logger.info("Reclaimed stale running job(s) on startup", extra={"count": reclaimed})
    except Exception:
        logger.exception("Failed to reclaim stale jobs on startup")
    logger.info("IO ETA worker starting", extra={"workloads": [w.value for w in WORKLOADS], "concurrency": CONCURRENCY})
    while True:
        await semaphore.acquire()
        try:
            async with AsyncSessionLocal() as session, session.begin():
                reserved = await _reserve_next_job(session)
        except Exception as exc:
            semaphore.release()
            logger.exception("ETA async worker loop error", extra={"error": str(exc)})
            await asyncio.sleep(POLL_INTERVAL)
            continue

        if not reserved:
            semaphore.release()
            await asyncio.sleep(POLL_INTERVAL)
            continue

        job_id, execution_id = reserved
        if execution_id:
            async with AsyncSessionLocal() as session, session.begin():
                await update_execution_state_async(session, execution_id)

        logger.info("ETA async job reserved", extra={"job_id": str(job_id)})
        asyncio.create_task(_handle_job(job_id, execution_id, semaphore))


def run_eta_worker_async_entrypoint() -> None:
    asyncio.run(run_eta_worker_async())
