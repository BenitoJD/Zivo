import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.db import SessionLocal
from app.eta import context as eta_context
from app.eta.execution_state import cancel_descendants_sync, update_execution_state_sync
from app.eta.handlers import cpu as _cpu_handlers  # noqa: F401
from app.eta.lease import HEARTBEAT_INTERVAL_SECONDS, job_duration_exceeded, lease_deadline_from, renew_lease_sync
from app.eta.priority import priority_sort_key
from app.eta.registry import get_handler, normalize_result
from app.eta.stale_jobs import (
    STALE_REAPER_INTERVAL_SECONDS,
    reclaim_stale_jobs_sync,
)
from app.models import EtaJobDependency, Job, JobStatus, JobWorkload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_ID = os.getenv("ETA_WORKER_ID", str(uuid.uuid4()))
POLL_INTERVAL = float(os.getenv("ETA_WORKER_POLL_INTERVAL", "0.3"))
RETRY_DELAY_SECONDS = float(os.getenv("ETA_WORKER_RETRY_DELAY_SECONDS", "5.0"))
# CPU-workload jobs (incl. generate.questions) run concurrently in a thread pool.
# generate.questions is LLM-I/O-bound — threads overlap on network waits — and the
# reservation uses SELECT ... FOR UPDATE SKIP LOCKED so parallel slots never collide.
# Default to parallel now that the model is a concurrent hosted API. Total concurrent
# LLM calls ≈ this × ZIVO_GENERATION_CONCURRENCY (intra-batch gates); keep the product
# under the provider's rate limit (override via env). Also bounded by LLM_MAX_CONCURRENT.
MAX_CONCURRENCY = max(1, int(os.getenv("ETA_CPU_WORKER_MAX_CONCURRENCY", "4")))


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
    return workloads or [JobWorkload.cpu]


WORKLOADS = _parse_workloads(os.getenv("ETA_WORKER_WORKLOADS", "cpu"))

def _reclaim_stale_jobs(db: Session) -> int:
    """Reclaim orphaned 'running' jobs for this worker's workloads.

    Thin wrapper over the shared, heartbeat-keyed reaper (see
    :mod:`app.eta.stale_jobs`). Workload-scoped so the IO and CPU workers don't
    compete for the same orphan.
    """
    return reclaim_stale_jobs_sync(db, workloads=[w.value for w in WORKLOADS])


def _periodic_stale_reaper(stop_requested: Callable[[], bool]) -> None:
    while not stop_requested():
        time.sleep(STALE_REAPER_INTERVAL_SECONDS)
        try:
            with SessionLocal.begin() as db:
                reclaimed = _reclaim_stale_jobs(db)
            if reclaimed:
                logger.info("Reclaimed stale running job(s)", extra={"count": reclaimed})
        except Exception:
            logger.exception("Periodic stale job reaper failed")


def _reserve_next_job(db: Session) -> tuple[object, object] | None:
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
    deadline = lease_deadline_from()
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
    result = db.execute(
        update(Job)
        .where(Job.id == subquery.c.id)
        .values(
            status=JobStatus.running,
            locked_by=WORKER_ID,
            locked_at=func.now(),
            heartbeat_at=func.now(),
            lease_deadline=deadline,
            attempts=Job.attempts + 1,
            updated_at=func.now(),
        )
        .returning(Job.id, Job.execution_id)
    )
    row = result.first()
    return (row[0], row[1]) if row else None


def _load_job_snapshot(db: Session, job_id: str) -> tuple[str, dict, int, int, object, datetime | None] | None:
    job = db.get(Job, job_id)
    if not job:
        return None
    return job.name, job.payload, job.attempts, job.max_attempts, job.execution_id, job.locked_at


def _mark_succeeded(db: Session, job_id: str, result: dict, execution_id: object) -> None:
    result_row = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.running)
        .values(
            status=JobStatus.succeeded,
            result=result,
            error=None,
            locked_by=None,
            locked_at=None,
            heartbeat_at=None,
            lease_deadline=None,
            finished_at=func.now(),
            updated_at=func.now(),
        )
    )
    if result_row.rowcount == 0:
        return
    if execution_id:
        update_execution_state_sync(db, execution_id)


def _mark_failed(
    db: Session,
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

    result_row = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.running)
        .values(
            status=status,
            error=error,
            run_after=run_after,
            locked_by=None,
            locked_at=None,
            heartbeat_at=None,
            lease_deadline=None,
            finished_at=func.now() if status == JobStatus.failed else None,
            updated_at=func.now(),
        )
    )
    if result_row.rowcount == 0:
        return
    if status == JobStatus.failed:
        cancel_descendants_sync(
            db,
            job_id=job_id,
            error_message=f"Upstream dependency failed: {job_id}",
        )
    if execution_id:
        update_execution_state_sync(db, execution_id)


def _process_job(job_id: object, job_name: str, payload: dict) -> dict:
    token = eta_context.set_current_job_id(job_id)
    handler = get_handler(job_name)
    try:
        if not handler:
            raise RuntimeError(f"Unknown job name: {job_name}")

        if handler.input_model:
            payload = handler.input_model.model_validate(payload)
        # The CPU worker is intentionally synchronous. Async handlers belong
        # in the IO worker (`run_eta_worker_async.py`), which already routes
        # them through the running event loop. Spawning a fresh loop here
        # races with library code that caches `asyncio.get_event_loop()`.
        if handler.fn is None:
            raise RuntimeError(f"Handler {job_name} has no callable")
        result = handler.fn(payload)
        return normalize_result(handler, result)
    finally:
        eta_context.reset_current_job_id(token)


def _heartbeat_loop(job_id: object, started_at: datetime | None, stop: threading.Event) -> None:
    """Renew this job's lease while it runs, so a long handler is never
    false-reclaimed by the stale reaper. Stops when ``stop`` is set (handler
    done) or the worker is draining.

    Watchdog: once ``started_at`` is older than JOB_MAX_DURATION_SECONDS, stop
    renewing the lease even though the handler is still alive — a hung handler
    (provider call that never returns, network stall) would otherwise heartbeat
    forever and the reaper would never reclaim it. The stale reaper then
    requeues (retry) or fails (attempts exhausted) the job.
    """
    interval = max(1.0, HEARTBEAT_INTERVAL_SECONDS)
    while not stop.is_set():
        if job_duration_exceeded(started_at):
            logger.warning(
                "ETA job exceeded max duration; releasing lease for reaper",
                extra={"job_id": str(job_id)},
            )
            return
        try:
            with SessionLocal() as db:
                renew_lease_sync(db, job_id)
                db.commit()
        except Exception:
            logger.debug("heartbeat renew failed", extra={"job_id": str(job_id)}, exc_info=True)
        stop.wait(interval)


def _handle_reserved_job(job_id: object, execution_id: object) -> None:
    if execution_id:
        with SessionLocal.begin() as db:
            update_execution_state_sync(db, execution_id)

    logger.info("ETA job reserved", extra={"job_id": str(job_id)})

    with SessionLocal() as db:
        snapshot = _load_job_snapshot(db, job_id)
    if not snapshot:
        logger.warning("ETA job snapshot missing after reservation", extra={"job_id": str(job_id)})
        return

    job_name, payload, attempts, max_attempts, execution_id, started_at = snapshot
    logger.info("ETA job started", extra={"job_id": str(job_id), "job_name": job_name, "attempt": attempts, "max_attempts": max_attempts})
    # Renew the lease for as long as the handler runs. If this pod dies, the
    # lease expires and the reaper requeues the job — no false reclaim while live.
    # The watchdog releases the lease if the job outlives JOB_MAX_DURATION_SECONDS.
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(job_id, started_at, heartbeat_stop),
        name=f"eta-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        result = _process_job(job_id, job_name, payload)
        with SessionLocal.begin() as db:
            _mark_succeeded(db, job_id, result, execution_id)
        logger.info("ETA job succeeded", extra={"job_id": str(job_id)})
    except Exception as exc:
        with SessionLocal.begin() as db:
            _mark_failed(db, job_id, attempts, max_attempts, str(exc), execution_id)
        logger.exception("ETA job failed", extra={"job_id": str(job_id)})
    finally:
        heartbeat_stop.set()


def _reap_completed_jobs(running_jobs: dict[Future, str]) -> None:
    for future, job_id in list(running_jobs.items()):
        if not future.done():
            continue
        try:
            future.result()
        except Exception as exc:
            logger.exception("ETA worker job thread failed unexpectedly", extra={"job_id": str(job_id), "error": str(exc)})
        running_jobs.pop(future, None)
        logger.info("ETA job slot released", extra={"job_id": str(job_id), "active_jobs": len(running_jobs), "max_concurrency": MAX_CONCURRENCY})


def _wait_for_job_activity(running_jobs: dict[Future, str], timeout: float | None = None) -> None:
    if not running_jobs:
        return
    wait(set(running_jobs.keys()), timeout=timeout, return_when=FIRST_COMPLETED)


def run_eta_worker(should_stop: Callable[[], bool] | None = None) -> None:
    stop_requested = should_stop or (lambda: False)
    logger.info("CPU ETA worker starting", extra={"workloads": [w.value for w in WORKLOADS], "max_concurrency": MAX_CONCURRENCY})
    if JobWorkload.cpu in WORKLOADS:
        try:
            from app.services.embed import get_embedder, set_active_embed_model
            from app.services.llm_registry import bootstrap_llm_registry_from_env, resolve_embedding_model

            with SessionLocal() as db:
                bootstrap_llm_registry_from_env(db)
                try:
                    embed = resolve_embedding_model(db)
                    set_active_embed_model(embed.record.litellm_model)
                except Exception:
                    logger.debug("embedding model resolve failed; using default", exc_info=True)
            get_embedder()
            logger.info("Embedding model preloaded")
        except Exception:
            logger.exception("Failed to preload embedding model")
    # Reclaim jobs orphaned by a prior worker crash before entering the loop.
    from app.eta.job_notify import ensure_eta_notify_listener, wait_eta_job_notify

    ensure_eta_notify_listener()
    try:
        with SessionLocal.begin() as db:
            reclaimed = _reclaim_stale_jobs(db)
        if reclaimed:
            logger.info("Reclaimed stale running job(s) on startup", extra={"count": reclaimed})
    except Exception:
        logger.exception("Failed to reclaim stale jobs on startup")
    reaper_stop = threading.Event()
    reaper = threading.Thread(
        target=_periodic_stale_reaper,
        args=(reaper_stop.is_set,),
        name="eta-cpu-stale-reaper",
        daemon=True,
    )
    reaper.start()
    with ThreadPoolExecutor(
        max_workers=MAX_CONCURRENCY, thread_name_prefix="eta-cpu-worker"
    ) as executor:
        running_jobs: dict[Future, str] = {}
        while True:
            _reap_completed_jobs(running_jobs)
            if stop_requested() and not running_jobs:
                reaper_stop.set()
                break

            if len(running_jobs) >= MAX_CONCURRENCY:
                _wait_for_job_activity(running_jobs)
                continue

            if stop_requested():
                _wait_for_job_activity(running_jobs, timeout=POLL_INTERVAL)
                continue

            try:
                with SessionLocal.begin() as db:
                    reserved = _reserve_next_job(db)
            except Exception as exc:
                logger.exception("ETA worker loop error", extra={"error": str(exc)})
                if running_jobs:
                    _wait_for_job_activity(running_jobs, timeout=POLL_INTERVAL)
                else:
                    wait_eta_job_notify(POLL_INTERVAL)
                continue

            if not reserved:
                if running_jobs:
                    _wait_for_job_activity(running_jobs, timeout=POLL_INTERVAL)
                else:
                    wait_eta_job_notify(POLL_INTERVAL)
                continue

            job_id, execution_id = reserved
            future = executor.submit(_handle_reserved_job, job_id, execution_id)
            running_jobs[future] = str(job_id)
            logger.info(
                "ETA job dispatched",
                extra={
                    "job_id": str(job_id),
                    "active_jobs": len(running_jobs),
                    "max_concurrency": MAX_CONCURRENCY,
                },
            )
