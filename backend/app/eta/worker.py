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
from app.engine_runtime import Pred, Rule, apply, first_match, pick
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
from app.services.job_lifecycle import evaluate_job_lifecycle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_ID = os.getenv("ETA_WORKER_ID", str(uuid.uuid4()))
POLL_INTERVAL = float(os.getenv("ETA_WORKER_POLL_INTERVAL", "0.3"))
RETRY_DELAY_SECONDS = float(os.getenv("ETA_WORKER_RETRY_DELAY_SECONDS", "5.0"))
# CPU-workload jobs (incl. generate.questions) run concurrently in a thread pool.
# generate.questions is LLM-I/O-bound: threads overlap on network waits, and the
# reservation uses SELECT ... FOR UPDATE SKIP LOCKED so parallel slots never collide.
# Default to parallel now that the model is a concurrent hosted API. Total concurrent
# LLM calls ≈ this × ZIVO_GENERATION_CONCURRENCY (intra-batch gates); keep the product
# under the provider's rate limit (override via env). Also bounded by LLM_MAX_CONCURRENT.
MAX_CONCURRENCY = max(1, int(os.getenv("ETA_CPU_WORKER_MAX_CONCURRENCY", "4")))

_LOOP_RULES = (
    Rule(when=(Pred("stop", "truthy"), Pred("idle", "truthy")), action="exit"),
    Rule(when=(Pred("full", "truthy"),), action="wait_slot"),
    Rule(when=(Pred("stop", "truthy"),), action="wait_drain"),
    Rule(when=(), action="reserve"),
)


def _parse_workloads(value: str) -> Sequence[JobWorkload]:
    def _one(raw: str) -> JobWorkload | None:
        stripped = raw.strip()

        def _parse() -> JobWorkload | None:
            try:
                return JobWorkload(stripped)
            except ValueError:
                return None

        return pick(not stripped, lambda: None, _parse)

    parsed = list(filter(None, map(_one, value.split(","))))
    return parsed or [JobWorkload.cpu]


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
            pick(
                bool(reclaimed),
                lambda: logger.info("Reclaimed stale running job(s)", extra={"count": reclaimed}),
                lambda: None,
            )
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
    return pick(row is None, lambda: None, lambda: (row[0], row[1]))


def _load_job_snapshot(db: Session, job_id: str) -> tuple[str, dict, int, int, object, datetime | None] | None:
    job = db.get(Job, job_id)
    return pick(
        job is None,
        lambda: None,
        lambda: (job.name, job.payload, job.attempts, job.max_attempts, job.execution_id, job.locked_at),
    )


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

    def _after() -> None:
        pick(bool(execution_id), lambda: update_execution_state_sync(db, execution_id), lambda: None)

    pick(result_row.rowcount == 0, lambda: None, _after)


def _fail_lifecycle_handlers(*, attempts: int, max_attempts: int):
    verdict = evaluate_job_lifecycle(status="failed", retries_left=attempts < max_attempts)
    values = apply(
        verdict.action,
        {
            "retry": lambda: (
                JobStatus.queued,
                datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_SECONDS),
                None,
            ),
            "reclaim": lambda: (
                JobStatus.queued,
                datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_SECONDS),
                None,
            ),
            "fail": lambda: (JobStatus.failed, None, func.now()),
            "keep": lambda: (JobStatus.failed, None, func.now()),
        },
    )
    return verdict, values


def _mark_failed(
    db: Session,
    job_id: str,
    attempts: int,
    max_attempts: int,
    error: str,
    execution_id: object,
) -> None:
    verdict, (status, run_after, finished_at) = _fail_lifecycle_handlers(
        attempts=attempts, max_attempts=max_attempts
    )
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
            finished_at=finished_at,
            updated_at=func.now(),
        )
    )

    def _after() -> None:
        apply(
            verdict.action,
            {
                "fail": lambda: cancel_descendants_sync(
                    db,
                    job_id=job_id,
                    error_message=f"Upstream dependency failed: {job_id}",
                ),
                "keep": lambda: cancel_descendants_sync(
                    db,
                    job_id=job_id,
                    error_message=f"Upstream dependency failed: {job_id}",
                ),
                "retry": lambda: None,
                "reclaim": lambda: None,
            },
        )
        pick(bool(execution_id), lambda: update_execution_state_sync(db, execution_id), lambda: None)

    pick(result_row.rowcount == 0, lambda: None, _after)


def _process_job(job_id: object, job_name: str, payload: dict) -> dict:
    token = eta_context.set_current_job_id(job_id)
    handler = get_handler(job_name)
    try:
        def _missing() -> dict:
            raise RuntimeError(f"Unknown job name: {job_name}")

        def _run() -> dict:
            def _no_fn() -> None:
                raise RuntimeError(f"Handler {job_name} has no callable")

            pick(handler.fn is None, _no_fn, lambda: None)
            validated = pick(
                handler.input_model is None,
                lambda: payload,
                lambda: handler.input_model.model_validate(payload),
            )
            result = handler.fn(validated)
            return normalize_result(handler, result)

        return pick(handler is None, _missing, _run)
    finally:
        eta_context.reset_current_job_id(token)


def _heartbeat_loop(job_id: object, started_at: datetime | None, stop: threading.Event) -> None:
    """Renew this job's lease while it runs, so a long handler is never
    false-reclaimed by the stale reaper. Stops when ``stop`` is set (handler
    done) or the worker is draining.

    Watchdog: once ``started_at`` is older than JOB_MAX_DURATION_SECONDS, stop
    renewing the lease even though the handler is still alive. A hung handler
    (provider call that never returns, network stall) would otherwise heartbeat
    forever and the reaper would never reclaim it. The stale reaper then
    requeues (retry) or fails (attempts exhausted) the job.
    """
    interval = max(1.0, HEARTBEAT_INTERVAL_SECONDS)
    alive = True
    while (not stop.is_set()) and alive:
        exceeded = job_duration_exceeded(started_at)
        alive = not exceeded

        def _renew() -> None:
            try:
                with SessionLocal() as db:
                    renew_lease_sync(db, job_id)
                    db.commit()
            except Exception:
                logger.debug("heartbeat renew failed", extra={"job_id": str(job_id)}, exc_info=True)
            stop.wait(interval)

        pick(
            exceeded,
            lambda: logger.warning(
                "ETA job exceeded max duration; releasing lease for reaper",
                extra={"job_id": str(job_id)},
            ),
            _renew,
        )


def _handle_reserved_job(job_id: object, execution_id: object) -> None:
    def _touch_execution() -> None:
        with SessionLocal.begin() as db:
            update_execution_state_sync(db, execution_id)

    pick(bool(execution_id), _touch_execution, lambda: None)
    logger.info("ETA job reserved", extra={"job_id": str(job_id)})

    with SessionLocal() as db:
        snapshot = _load_job_snapshot(db, job_id)

    def _missing() -> None:
        logger.warning("ETA job snapshot missing after reservation", extra={"job_id": str(job_id)})

    def _run() -> None:
        job_name, payload, attempts, max_attempts, execution_id_snap, started_at = snapshot
        logger.info(
            "ETA job started",
            extra={
                "job_id": str(job_id),
                "job_name": job_name,
                "attempt": attempts,
                "max_attempts": max_attempts,
            },
        )
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
                _mark_succeeded(db, job_id, result, execution_id_snap)
            logger.info("ETA job succeeded", extra={"job_id": str(job_id)})
        except Exception as exc:
            with SessionLocal.begin() as db:
                _mark_failed(db, job_id, attempts, max_attempts, str(exc), execution_id_snap)
            logger.exception("ETA job failed", extra={"job_id": str(job_id)})
        finally:
            heartbeat_stop.set()

    pick(snapshot is None, _missing, _run)


def _reap_completed_jobs(running_jobs: dict[Future, str]) -> None:
    def _reap(item: tuple[Future, str]) -> None:
        future, job_id = item

        def _finish() -> None:
            try:
                future.result()
            except Exception as exc:
                logger.exception(
                    "ETA worker job thread failed unexpectedly",
                    extra={"job_id": str(job_id), "error": str(exc)},
                )
            running_jobs.pop(future, None)
            logger.info(
                "ETA job slot released",
                extra={
                    "job_id": str(job_id),
                    "active_jobs": len(running_jobs),
                    "max_concurrency": MAX_CONCURRENCY,
                },
            )

        pick(future.done(), _finish, lambda: None)

    for item in list(running_jobs.items()):
        _reap(item)


def _wait_for_job_activity(running_jobs: dict[Future, str], timeout: float | None = None) -> None:
    pick(
        not running_jobs,
        lambda: None,
        lambda: wait(set(running_jobs.keys()), timeout=timeout, return_when=FIRST_COMPLETED),
    )


def run_eta_worker(should_stop: Callable[[], bool] | None = None) -> None:
    stop_requested = should_stop or (lambda: False)
    logger.info(
        "CPU ETA worker starting",
        extra={"workloads": [w.value for w in WORKLOADS], "max_concurrency": MAX_CONCURRENCY},
    )

    def _preload() -> None:
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

    pick(JobWorkload.cpu in WORKLOADS, _preload, lambda: None)
    from app.eta.job_notify import ensure_eta_notify_listener, wait_eta_job_notify

    ensure_eta_notify_listener()
    try:
        with SessionLocal.begin() as db:
            reclaimed = _reclaim_stale_jobs(db)
        pick(
            bool(reclaimed),
            lambda: logger.info(
                "Reclaimed stale running job(s) on startup", extra={"count": reclaimed}
            ),
            lambda: None,
        )
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

        def _idle_wait() -> bool:
            pick(
                bool(running_jobs),
                lambda: _wait_for_job_activity(running_jobs, timeout=POLL_INTERVAL),
                lambda: wait_eta_job_notify(POLL_INTERVAL),
            )
            return True

        def _exit() -> bool:
            reaper_stop.set()
            return False

        def _wait_slot() -> bool:
            _wait_for_job_activity(running_jobs)
            return True

        def _wait_drain() -> bool:
            _wait_for_job_activity(running_jobs, timeout=POLL_INTERVAL)
            return True

        def _do_reserve() -> bool:
            try:
                with SessionLocal.begin() as db:
                    reserved = _reserve_next_job(db)
            except Exception as exc:
                logger.exception("ETA worker loop error", extra={"error": str(exc)})
                _idle_wait()
                return True

            def _miss() -> bool:
                _idle_wait()
                return True

            def _hit() -> bool:
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
                return True

            return pick(reserved is None, _miss, _hit)

        running = True
        while running:
            _reap_completed_jobs(running_jobs)
            hit = first_match(
                _LOOP_RULES,
                {
                    "stop": stop_requested(),
                    "idle": not running_jobs,
                    "full": len(running_jobs) >= MAX_CONCURRENCY,
                },
            )
            running = apply(
                hit.action,
                {
                    "exit": _exit,
                    "wait_slot": _wait_slot,
                    "wait_drain": _wait_drain,
                    "reserve": _do_reserve,
                },
            )
