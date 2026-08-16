"""Shared stale-job reclaim for ETA workers and the scheduler.

A job is *orphaned* when its lease has expired: ``lease_deadline < now`` (the
renewed deadline) OR, for rows that pre-date the lease columns, ``locked_at``
older than the legacy :data:`STALE_RUNNING_TIMEOUT_SECONDS` fallback. A live
worker renews ``heartbeat_at`` / ``lease_deadline`` every HEARTBEAT_INTERVAL
(see :mod:`app.eta.lease`), so a job genuinely in flight never crosses this
cutoff, which is the whole point: no more false reclaims on long-running jobs.

Reclaim re-queues the job and:
  * does NOT increment ``attempts``: only a real handler exception costs an
    attempt, so a job orphaned by N deploys is not auto-failed for that;
  * marks the job ``failed`` (and cancels DAG descendants) if ``attempts`` has
    already hit ``max_attempts``, uniformly for both IO and CPU workloads
    (the old IO reaper requeued forever).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine_runtime import apply, pick
from app.eta.execution_state import (
    cancel_descendants_async,
    cancel_descendants_sync,
    update_execution_state_async,
    update_execution_state_sync,
)
from app.models import Job, JobStatus
from app.services.job_lifecycle import evaluate_job_lifecycle

# Legacy fallback: rows with NULL lease_deadline (pre-migration or unmigrated
# writers) are reclaimed once locked_at is older than this. Kept generous so an
# in-flight job on a not-yet-restarted pod is not touched.
STALE_RUNNING_TIMEOUT_SECONDS = float(os.getenv("ETA_STALE_RUNNING_TIMEOUT", "600"))
STALE_REAPER_INTERVAL_SECONDS = float(os.getenv("ETA_STALE_REAPER_INTERVAL", "60"))

# A ``queued`` job that has sat un-run this long is treated as stale by the
# raw-SQL liveness check (ACTIVE_JOB_LIVENESS_SQL), so it no longer masks a
# stuck document from recovery. Generous vs. any real queue backpressure; the
# intent is only to catch jobs that will *never* run: a DAG child orphaned by
# a cancelled parent, or a row enqueued for a dead worker pool. A legitimately
# deferred job (future ``run_after``) is always considered live regardless of
# age. ``attempts >= max_attempts`` queued rows are also stale here.
STALE_QUEUED_TIMEOUT_SECONDS = float(os.getenv("ETA_STALE_QUEUED_TIMEOUT", "1800"))


def stale_running_cutoff(now: datetime | None = None) -> datetime:
    """Legacy ``locked_at`` cutoff, retained for the document-recovery schedules
    and any path that still keys off ``locked_at``.

    Rows written with a lease use :func:`app.eta.lease.stale_heartbeat_cutoff`.
    """
    resolved = now or datetime.now(timezone.utc)
    return resolved - timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS)


def stale_queued_cutoff(now: datetime | None = None) -> datetime:
    """``created_at`` cutoff past which an un-run ``queued`` job is stale."""
    resolved = now or datetime.now(timezone.utc)
    return resolved - timedelta(seconds=STALE_QUEUED_TIMEOUT_SECONDS)


# Raw-SQL mirror of the *inverse* of :func:`_orphaned_clause`. A job counts as
# "active" (live work, do not treat its document as stranded) when:
#   * ``queued`` AND recent enough to plausibly still be picked up, OR with a
#     future ``run_after`` (a legitimately deferred job), AND not already
#     exhausted (``attempts < max_attempts``). The age bound closes the hole
#     where a queued job that will *never* run (DAG child of a cancelled parent,
#     row for a dead worker pool) masked a stuck document from recovery forever.
#   * ``running`` with a valid lease (``lease_deadline >= NOW()``), or a legacy
#     row within the old ``locked_at`` cutoff.
# Used by the raw-SQL recovery paths (rag_window, ingest/newspaper recovery) so a
# long, actively-heartbeating job is never mistaken for dead and re-enqueued as a
# duplicate. Aliases the jobs table as ``j`` and binds ``:stale_cutoff`` (a
# ``stale_running_cutoff()`` datetime) and ``:queued_cutoff`` (a
# ``stale_queued_cutoff()`` datetime).
ACTIVE_JOB_LIVENESS_SQL = """
(
    (
        j.status = 'queued'
        AND j.attempts < j.max_attempts
        AND (
            j.run_after IS NOT NULL AND j.run_after >= NOW()
            OR j.created_at >= :queued_cutoff
        )
    )
    OR (
        j.status = 'running'
        AND (
            j.lease_deadline IS NOT NULL AND j.lease_deadline >= NOW()
            OR (
                j.lease_deadline IS NULL
                AND j.locked_at IS NOT NULL
                AND j.locked_at >= :stale_cutoff
            )
        )
    )
)
"""


def _orphaned_clause(now: datetime):
    """SQL predicate: the job's lease has expired.

    EITHER its renewed ``lease_deadline`` has passed OR it has no lease deadline
    yet and its legacy ``locked_at`` is past the old timeout.
    """
    legacy_cutoff = stale_running_cutoff(now)
    return or_(
        (Job.lease_deadline.is_not(None)) & (Job.lease_deadline < now),
        (Job.lease_deadline.is_(None))
        & (Job.locked_at.is_not(None))
        & (Job.locked_at < legacy_cutoff),
    )


def _fail_values() -> dict:
    return {
        "status": JobStatus.failed,
        "error": "Orphaned after worker exit (attempts exhausted)",
        "locked_by": None,
        "locked_at": None,
        "heartbeat_at": None,
        "lease_deadline": None,
        "finished_at": func.now(),
        "updated_at": func.now(),
    }


def _requeue_values() -> dict:
    return {
        "status": JobStatus.queued,
        "locked_by": None,
        "locked_at": None,
        "heartbeat_at": None,
        "lease_deadline": None,
        "error": None,
        "updated_at": func.now(),
    }


def _fail_exhausted_sync(db: Session, workloads: Sequence[str], orphaned) -> int:
    failed = db.execute(
        update(Job)
        .where(Job.status == JobStatus.running)
        .where(Job.workload.in_(list(workloads)))
        .where(orphaned)
        .where(Job.attempts >= Job.max_attempts)
        .values(**_fail_values())
        .returning(Job.id, Job.execution_id)
    )
    exhausted_rows = failed.all()

    def _one(job_id, execution_id) -> None:
        cancel_descendants_sync(
            db,
            job_id=job_id,
            error_message=f"Upstream dependency failed (orphaned): {job_id}",
        )
        pick(bool(execution_id), lambda: update_execution_state_sync(db, execution_id), lambda: None)

    for job_id, execution_id in exhausted_rows:
        _one(job_id, execution_id)
    return len(exhausted_rows)


def _requeue_orphans_sync(db: Session, workloads: Sequence[str], orphaned) -> int:
    requeued = db.execute(
        update(Job)
        .where(Job.status == JobStatus.running)
        .where(Job.workload.in_(list(workloads)))
        .where(orphaned)
        .values(**_requeue_values())
    )
    return requeued.rowcount or 0


async def _fail_exhausted_async(session: AsyncSession, workloads: Sequence[str], orphaned) -> int:
    failed = await session.execute(
        update(Job)
        .where(Job.status == JobStatus.running)
        .where(Job.workload.in_(list(workloads)))
        .where(orphaned)
        .where(Job.attempts >= Job.max_attempts)
        .values(**_fail_values())
        .returning(Job.id, Job.execution_id)
    )
    exhausted_rows = failed.all()

    async def _one(job_id, execution_id) -> None:
        await cancel_descendants_async(
            session,
            job_id=job_id,
            error_message=f"Upstream dependency failed (orphaned): {job_id}",
        )
        async def _skip() -> None:
            return None

        await pick(
            bool(execution_id),
            lambda: update_execution_state_async(session, execution_id),
            _skip,
        )

    for job_id, execution_id in exhausted_rows:
        await _one(job_id, execution_id)
    return len(exhausted_rows)


async def _requeue_orphans_async(session: AsyncSession, workloads: Sequence[str], orphaned) -> int:
    requeued = await session.execute(
        update(Job)
        .where(Job.status == JobStatus.running)
        .where(Job.workload.in_(list(workloads)))
        .where(orphaned)
        .values(**_requeue_values())
    )
    return requeued.rowcount or 0


def reclaim_stale_jobs_sync(
    db: Session,
    *,
    workloads: Sequence[str],
    now: datetime | None = None,
) -> int:
    """Reclaim orphaned ``running`` jobs for the given workloads.

    Returns the number of rows touched. Idempotent and safe to run concurrently:
    ``UPDATE ... WHERE status='running'`` only matches rows still in flight, and
    the workload filter keeps IO and CPU reclaim from competing.
    """
    resolved_now = now or datetime.now(timezone.utc)
    orphaned = _orphaned_clause(resolved_now)
    fail_verdict = evaluate_job_lifecycle(status="failed", retries_left=False)
    reclaim_verdict = evaluate_job_lifecycle(status="running", stale_running=True)
    exhausted = apply(
        fail_verdict.action,
        {
            "fail": lambda: _fail_exhausted_sync(db, workloads, orphaned),
            "keep": lambda: 0,
            "reclaim": lambda: 0,
            "retry": lambda: 0,
        },
    )
    requeued = apply(
        reclaim_verdict.action,
        {
            "reclaim": lambda: _requeue_orphans_sync(db, workloads, orphaned),
            "retry": lambda: _requeue_orphans_sync(db, workloads, orphaned),
            "fail": lambda: 0,
            "keep": lambda: 0,
        },
    )
    return exhausted + requeued


async def reclaim_stale_jobs_async(
    session: AsyncSession,
    *,
    workloads: Sequence[str],
    now: datetime | None = None,
) -> int:
    """Async counterpart of :func:`reclaim_stale_jobs_sync`."""
    resolved_now = now or datetime.now(timezone.utc)
    orphaned = _orphaned_clause(resolved_now)
    fail_verdict = evaluate_job_lifecycle(status="failed", retries_left=False)
    reclaim_verdict = evaluate_job_lifecycle(status="running", stale_running=True)

    async def _zero() -> int:
        return 0

    exhausted = await apply(
        fail_verdict.action,
        {
            "fail": lambda: _fail_exhausted_async(session, workloads, orphaned),
            "keep": _zero,
            "reclaim": _zero,
            "retry": _zero,
        },
    )
    requeued = await apply(
        reclaim_verdict.action,
        {
            "reclaim": lambda: _requeue_orphans_async(session, workloads, orphaned),
            "retry": lambda: _requeue_orphans_async(session, workloads, orphaned),
            "fail": _zero,
            "keep": _zero,
        },
    )
    return exhausted + requeued
