"""Lease/heartbeat primitives for ETA job crash recovery.

A leased job carries ``lease_deadline`` (the instant after which the reaper may
treat it as orphaned) and ``heartbeat_at`` (renewed by the worker while it runs).
The worker calls :func:`renew_lease_sync` / :func:`renew_lease_async` every
:data:`HEARTBEAT_INTERVAL` seconds; each renewal pushes ``lease_deadline`` out by
:data:`LEASE_DURATION_SECONDS`. If the worker dies, the lease simply expires and
the reaper (see :mod:`app.eta.stale_jobs`) requeues the job — no time-guessing,
no false positives on long-running jobs.

Layered on top of the existing ``FOR UPDATE SKIP LOCKED`` claim; it does not
replace it. The pure-timing helpers (no ORM) live here so any module can read the
lease policy without pulling in SQLAlchemy; the DB-touching renew/release
operations are co-located for cohesion.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, update
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus

# --- Timing (env-overridable) -------------------------------------------------
# Safety lease: if the worker stops heartbeating, the job becomes reclaimable
# this many seconds after the last heartbeat. Must be >> HEARTBEAT_INTERVAL.
LEASE_DURATION_SECONDS = float(os.getenv("ETA_LEASE_DURATION_SECONDS", "120"))
# How often the worker renews the lease while a job runs. ~8x under the stale
# threshold so a single missed tick never false-reclaims.
HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("ETA_HEARTBEAT_INTERVAL_SECONDS", "15"))
# A job is orphaned if its heartbeat is older than this (the reaper's effective
# cutoff). Kept < LEASE_DURATION so reclaim happens promptly after a true crash.
STALE_THRESHOLD_SECONDS = float(os.getenv("ETA_STALE_THRESHOLD_SECONDS", "90"))
# Watchdog: the longest a single job may run before its lease stops being
# renewed, even if the handler is still alive. A handler that hangs (provider
# call that never returns, network stall, infinite loop) would otherwise keep
# heartbeating forever and the reaper would never reclaim it — the UI sits at
# "Processing…" indefinitely. Once the lease stops renewing, the reaper
# requeues (retry) or fails (attempts exhausted) the job within
# STALE_THRESHOLD_SECONDS. Must be larger than any legitimate job; tune per
# deployment via env. 0 disables the watchdog.
JOB_MAX_DURATION_SECONDS = float(os.getenv("ETA_JOB_MAX_DURATION_SECONDS", "3600"))


def lease_deadline_from(now: datetime | None = None) -> datetime:
    """Deadline for a lease taken/renewed *now*."""
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=LEASE_DURATION_SECONDS)


def job_duration_exceeded(started_at: datetime | None, now: datetime | None = None) -> bool:
    """True when a running job has outlived ``JOB_MAX_DURATION_SECONDS``.

    The worker's heartbeat loop checks this each tick: once exceeded, it stops
    renewing the lease so the stale reaper reclaims the job (hang → retry/fail
    instead of "Processing…" forever). ``started_at`` is the job's ``locked_at``.
    """
    if JOB_MAX_DURATION_SECONDS <= 0:
        return False
    if started_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - started_at).total_seconds() > JOB_MAX_DURATION_SECONDS


def stale_heartbeat_cutoff(now: datetime | None = None) -> datetime:
    """Heartbeats older than this are considered orphaned (worker died)."""
    now = now or datetime.now(timezone.utc)
    return now - timedelta(seconds=STALE_THRESHOLD_SECONDS)


def renew_lease_sync(db: Session, job_id: object) -> bool:
    """Renew the lease on a running job (a heartbeat). Returns True if renewed.

    Best-effort: only matches ``status='running'`` rows, so if the job already
    finished/failed on another path this no-ops.
    """
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.running)
        .values(
            heartbeat_at=datetime.now(timezone.utc),
            lease_deadline=lease_deadline_from(),
            updated_at=func.now(),
        )
    )
    return (result.rowcount or 0) > 0


async def renew_lease_async(session: AsyncSession, job_id: object) -> bool:
    """Async counterpart of :func:`renew_lease_sync`."""
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.running)
        .values(
            heartbeat_at=datetime.now(timezone.utc),
            lease_deadline=lease_deadline_from(),
            updated_at=func.now(),
        )
    )
    return (result.rowcount or 0) > 0
