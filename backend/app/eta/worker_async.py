import asyncio
import inspect
import logging
import os
import signal
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.db_search_path import (
    PGBOUNCER_ASYNCPG_CONNECT_ARGS,
    PGBOUNCER_ASYNCPG_RAW_CONNECT_ARGS,
    attach_search_path,
)
from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.eta import context as eta_context
from app.eta.scheduler_runtime import eta_scheduler_service
from app.eta.execution_state import cancel_descendants_async, update_execution_state_async
from app.eta.handlers import io as _io_handlers  # noqa: F401
from app.eta.lease import HEARTBEAT_INTERVAL_SECONDS, job_duration_exceeded, lease_deadline_from, renew_lease_async
from app.eta.llm_concurrency import LLM_MAX_CONCURRENT
from app.eta.priority import priority_sort_key
from app.eta.registry import get_handler, normalize_result
from app.eta.stale_jobs import (
    reclaim_stale_jobs_async,
)
from app.models import EtaJobDependency, Job, JobStatus, JobWorkload
from app.services.job_lifecycle import evaluate_job_lifecycle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()

WORKER_ID = os.getenv("ETA_WORKER_ID", str(uuid.uuid4()))
POLL_INTERVAL = float(os.getenv("ETA_WORKER_POLL_INTERVAL", "0.3"))
RETRY_DELAY_SECONDS = float(os.getenv("ETA_WORKER_RETRY_DELAY_SECONDS", "5.0"))
_ETA_NOTIFY_CHANNEL = "zivo_eta_job"
_job_wake = asyncio.Event()
_background_tasks: set[asyncio.Task] = set()
_STALE_REAPER_INTERVAL = float(os.getenv("ETA_STALE_REAPER_INTERVAL", "60"))

_DSN_PREFIXES = (
    "postgresql+psycopg://",
    "postgresql+asyncpg://",
    "postgresql://",
)

_ASYNC_URL_RULES = (
    Rule(when=(Pred("url", "startswith", "postgresql+psycopg://"),), action="psycopg"),
    Rule(when=(Pred("url", "startswith", "postgresql://"),), action="plain"),
    Rule(when=(), action="keep"),
)

_LOOP_RULES = (
    Rule(when=(Pred("stop", "truthy"), Pred("idle", "truthy")), action="exit"),
    Rule(when=(Pred("stop", "truthy"),), action="wait_drain"),
    Rule(when=(), action="reserve"),
)


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


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
    return parsed or [JobWorkload.io]


WORKLOADS = _parse_workloads(os.getenv("ETA_WORKER_WORKLOADS", "io"))
CONCURRENCY = int(os.getenv("ETA_IO_CONCURRENCY", "16"))


async def _async_noop() -> None:
    return None


async def _reclaim_stale_jobs(session: AsyncSession) -> int:
    """Reclaim orphaned 'running' jobs for this worker's workloads.

    Thin wrapper over the shared, heartbeat-keyed reaper (see
    :mod:`app.eta.stale_jobs`). Workload-scoped so the IO and CPU workers don't
    compete for the same orphan.
    """
    return await reclaim_stale_jobs_async(session, workloads=[w.value for w in WORKLOADS])


def _async_url() -> str:
    url = settings.database_url
    hit = first_match(_ASYNC_URL_RULES, {"url": url})
    return apply(
        hit.action,
        {
            "psycopg": lambda: url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1),
            "plain": lambda: url.replace("postgresql://", "postgresql+asyncpg://", 1),
            "keep": lambda: url,
        },
    )


# Keep pool small per process. PgBouncer (or similar) should multiplex
# client connections to Postgres in production.
engine = create_async_engine(
    _async_url(),
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    connect_args=PGBOUNCER_ASYNCPG_CONNECT_ARGS,
)
attach_search_path(engine.sync_engine)
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
    result = await session.execute(
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


async def _load_job_snapshot(
    session: AsyncSession, job_id: str
) -> tuple[str, dict, int, int, object, datetime | None] | None:
    job = await session.get(Job, job_id)
    return pick(
        job is None,
        lambda: None,
        lambda: (job.name, job.payload, job.attempts, job.max_attempts, job.execution_id, job.locked_at),
    )


async def _mark_succeeded(
    session: AsyncSession, job_id: str, result: dict, execution_id: object
) -> None:
    result_row = await session.execute(
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

    async def _after() -> None:
        await pick(
            bool(execution_id),
            lambda: update_execution_state_async(session, execution_id),
            _async_noop,
        )

    await pick(result_row.rowcount == 0, _async_noop, _after)


def _fail_lifecycle_values(*, attempts: int, max_attempts: int):
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


async def _mark_failed(
    session: AsyncSession,
    job_id: str,
    attempts: int,
    max_attempts: int,
    error: str,
    execution_id: object,
) -> None:
    verdict, (status, run_after, finished_at) = _fail_lifecycle_values(
        attempts=attempts, max_attempts=max_attempts
    )
    result_row = await session.execute(
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

    async def _cancel() -> None:
        await cancel_descendants_async(
            session,
            job_id=job_id,
            error_message=f"Upstream dependency failed: {job_id}",
        )

    async def _after() -> None:
        await apply(
            verdict.action,
            {
                "fail": _cancel,
                "keep": _cancel,
                "retry": _async_noop,
                "reclaim": _async_noop,
            },
        )
        await pick(
            bool(execution_id),
            lambda: update_execution_state_async(session, execution_id),
            _async_noop,
        )

    await pick(result_row.rowcount == 0, _async_noop, _after)


async def _process_job(job_id: object, job_name: str, payload: dict) -> dict:
    token = eta_context.set_current_job_id(job_id)
    handler = get_handler(job_name)
    try:
        def _missing() -> dict:
            raise RuntimeError(f"Unknown job name: {job_name}")

        async def _run() -> dict:
            validated = pick(
                handler.input_model is None,
                lambda: payload,
                lambda: handler.input_model.model_validate(payload),
            )
            result = await pick(
                inspect.iscoroutinefunction(handler.fn),
                lambda: handler.fn(validated),
                lambda: asyncio.to_thread(handler.fn, validated),
            )
            return normalize_result(handler, result)

        return await pick(handler is None, lambda: _missing(), _run)
    finally:
        eta_context.reset_current_job_id(token)


async def _heartbeat_loop(job_id: object, started_at: datetime | None, stop: asyncio.Event) -> None:
    """Renew this job's lease while it runs, so a long handler is never
    false-reclaimed by the stale reaper. Stops when ``stop`` is set (handler
    done) or the worker is draining.

    Watchdog: once ``started_at`` is older than JOB_MAX_DURATION_SECONDS, stop
    renewing the lease even though the handler is still alive. A hung handler
    would otherwise heartbeat forever and never be reclaimed. The stale reaper
    then requeues (retry) or fails (attempts exhausted) the job.
    """
    interval = max(1.0, HEARTBEAT_INTERVAL_SECONDS)
    alive = True
    while (not stop.is_set()) and alive:
        exceeded = job_duration_exceeded(started_at)
        alive = not exceeded

        async def _renew() -> None:
            try:
                async with AsyncSessionLocal() as session:
                    await renew_lease_async(session, job_id)
                    await session.commit()
            except Exception:
                logger.debug("heartbeat renew failed", extra={"job_id": str(job_id)}, exc_info=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass

        pick(
            exceeded,
            lambda: logger.warning(
                "ETA async job exceeded max duration; releasing lease for reaper",
                extra={"job_id": str(job_id)},
            ),
            lambda: None,
        )
        await pick(exceeded, _async_noop, _renew)


async def _handle_job(job_id: object, execution_id: object, semaphore: asyncio.Semaphore) -> None:
    try:
        async with AsyncSessionLocal() as session:
            snapshot = await _load_job_snapshot(session, job_id)

        async def _run() -> None:
            job_name, payload, attempts, max_attempts, execution_id_snap, started_at = snapshot
            logger.info(
                "ETA async job started",
                extra={
                    "job_id": str(job_id),
                    "job_name": job_name,
                    "attempt": attempts,
                    "max_attempts": max_attempts,
                },
            )
            heartbeat_stop = asyncio.Event()
            heartbeat_task = _spawn(_heartbeat_loop(job_id, started_at, heartbeat_stop))
            try:
                result = await _process_job(job_id, job_name, payload)
                async with AsyncSessionLocal() as session, session.begin():
                    await _mark_succeeded(session, job_id, result, execution_id_snap)
                logger.info("ETA async job succeeded", extra={"job_id": str(job_id)})
            except Exception as exc:
                async with AsyncSessionLocal() as session, session.begin():
                    await _mark_failed(session, job_id, attempts, max_attempts, str(exc), execution_id_snap)
                logger.exception("ETA async job failed", extra={"job_id": str(job_id)})
            finally:
                heartbeat_stop.set()
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass

        await pick(snapshot is None, _async_noop, _run)
    finally:
        semaphore.release()


def _notify_dsn() -> str:
    dsn = settings.database_url
    matched = next(filter(dsn.startswith, _DSN_PREFIXES), None)
    return pick(matched is None, lambda: dsn, lambda: "postgresql://" + dsn[len(matched) :])


async def _listen_for_job_notifications() -> None:
    """LISTEN/NOTIFY wake: reconnects on connection loss."""
    import asyncpg

    backoff = 1.0
    while True:
        conn = None
        try:
            conn = await asyncpg.connect(_notify_dsn(), **PGBOUNCER_ASYNCPG_RAW_CONNECT_ARGS)

            def _on_notify(*_args) -> None:
                _job_wake.set()

            await conn.add_listener(_ETA_NOTIFY_CHANNEL, _on_notify)
            logger.info("ETA NOTIFY listener connected", extra={"channel": _ETA_NOTIFY_CHANNEL})
            backoff = 1.0
            while not conn.is_closed():
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ETA job NOTIFY listener failed — reconnecting")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        finally:
            async def _close() -> None:
                try:
                    await conn.close()
                except Exception:
                    logger.debug("best-effort worker step failed", exc_info=True)

            await pick(conn is not None and not conn.is_closed(), _close, _async_noop)


async def _periodic_stale_reaper() -> None:
    while True:
        await asyncio.sleep(_STALE_REAPER_INTERVAL)
        try:
            async with AsyncSessionLocal() as session, session.begin():
                reclaimed = await _reclaim_stale_jobs(session)
            pick(
                bool(reclaimed),
                lambda: logger.info(
                    "Reclaimed stale running job(s)",
                    extra={"count": reclaimed},
                ),
                lambda: None,
            )
        except Exception:
            logger.exception("Periodic stale job reaper failed")


async def run_eta_worker_async(should_stop: Callable[[], bool] | None = None) -> None:
    stop_requested = should_stop or (lambda: False)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    in_flight: set[asyncio.Task] = set()
    eta_scheduler_service.start()
    _spawn(_listen_for_job_notifications())
    _spawn(_periodic_stale_reaper())
    try:
        try:
            async with AsyncSessionLocal() as session, session.begin():
                reclaimed = await _reclaim_stale_jobs(session)
            pick(
                bool(reclaimed),
                lambda: logger.info(
                    "Reclaimed stale running job(s) on startup", extra={"count": reclaimed}
                ),
                lambda: None,
            )
        except Exception:
            logger.exception("Failed to reclaim stale jobs on startup")
        logger.info(
            "IO ETA worker starting",
            extra={
                "workloads": [w.value for w in WORKLOADS],
                "concurrency": CONCURRENCY,
                "llm_max_concurrent": LLM_MAX_CONCURRENT,
            },
        )

        async def _exit() -> bool:
            logger.info("IO ETA worker drained — exiting")
            return False

        async def _wait_drain() -> bool:
            await asyncio.sleep(POLL_INTERVAL)
            return True

        async def _touch_exec(execution_id: object) -> None:
            async with AsyncSessionLocal() as session, session.begin():
                await update_execution_state_async(session, execution_id)

        async def _do_reserve() -> bool:
            await semaphore.acquire()
            try:
                async with AsyncSessionLocal() as session, session.begin():
                    reserved = await _reserve_next_job(session)
            except Exception as exc:
                semaphore.release()
                logger.exception("ETA async worker loop error", extra={"error": str(exc)})
                await asyncio.sleep(POLL_INTERVAL)
                return True

            async def _miss() -> bool:
                semaphore.release()
                try:
                    await asyncio.wait_for(_job_wake.wait(), timeout=POLL_INTERVAL)
                except TimeoutError:
                    pass
                _job_wake.clear()
                return True

            async def _hit() -> bool:
                job_id, execution_id = reserved
                await pick(bool(execution_id), lambda: _touch_exec(execution_id), _async_noop)
                logger.info("ETA async job reserved", extra={"job_id": str(job_id)})
                task = asyncio.create_task(_handle_job(job_id, execution_id, semaphore))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
                return True

            return await pick(reserved is None, _miss, _hit)

        running = True
        while running:
            hit = first_match(
                _LOOP_RULES,
                {"stop": stop_requested(), "idle": not in_flight},
            )
            running = await apply(
                hit.action,
                {
                    "exit": _exit,
                    "wait_drain": _wait_drain,
                    "reserve": _do_reserve,
                },
            )
    finally:
        eta_scheduler_service.stop()


def run_eta_worker_async_entrypoint() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop_flag = asyncio.Event()

    def _request_stop(*_args) -> None:
        logger.info("IO ETA worker received shutdown signal — draining")
        loop.call_soon_threadsafe(stop_flag.set)

    def _add_handler(sig) -> None:
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: stop_flag.set())

    for sig in (signal.SIGTERM, signal.SIGINT):
        _add_handler(sig)

    loop.run_until_complete(run_eta_worker_async(should_stop=stop_flag.is_set))
