"""Scheduler-driven reclaim of orphaned ETA jobs.

The worker-internal stale reaper only runs inside worker pods. If *all* worker
pods are down (node failure, bad rollout), nothing reaps until a replacement
starts. This schedule runs on the IO worker's in-process scheduler and reclaims
orphaned ``running`` jobs across **all** workloads. See :mod:`app.eta.stale_jobs`
for the orphan definition.
"""

from __future__ import annotations

import logging

from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.eta.stale_jobs import reclaim_stale_jobs_sync
from app.models import JobWorkload

logger = logging.getLogger(__name__)

_ALL_WORKLOADS = [JobWorkload.io.value, JobWorkload.cpu.value]


@eta_scheduler(every_minutes=2, key="jobs.reclaim_stale")
def run_jobs_reclaim() -> None:
    with SessionLocal() as db:
        reclaimed = reclaim_stale_jobs_sync(db, workloads=_ALL_WORKLOADS)
        db.commit()
    if reclaimed:
        logger.info("scheduler reclaimed orphaned job(s)", extra={"count": reclaimed})
