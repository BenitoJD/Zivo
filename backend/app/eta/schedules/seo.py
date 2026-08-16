"""ETA schedules for SEO /learn cook + SD daily floor."""

from __future__ import annotations

import logging

from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.models import JobWorkload
from app.services.jobs import enqueue_job
from app.services.seo_gate import SEO_COOK_TICK_DEFAULT

logger = logging.getLogger(__name__)


@eta_scheduler(every_minutes=60, key="seo.cook_batch")
def run_seo_cook_batch() -> None:
    """Enqueue cook work — heavy LLM stays in the ETA handler."""
    with SessionLocal() as db:
        from app.repositories import seo as seo_repo

        settings = seo_repo.get_settings(db)
        if not settings.get("cook_enabled"):
            return
        enqueue_job(
            db,
            name="seo.cook_batch",
            workload=JobWorkload.io,
            payload={"limit": SEO_COOK_TICK_DEFAULT},
        )


@eta_scheduler(every_minutes=360, key="seo.ensure_sd_daily")
def run_seo_ensure_sd_daily() -> None:
    with SessionLocal() as db:
        from app.repositories import seo as seo_repo

        settings = seo_repo.get_settings(db)
        if not settings.get("cook_enabled"):
            return
        enqueue_job(
            db,
            name="seo.ensure_sd_daily",
            workload=JobWorkload.io,
            payload={},
        )
