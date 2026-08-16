"""ETA schedules for SEO /learn cook + SD daily floor."""

from __future__ import annotations

import logging

from app.db import SessionLocal
from app.engine_runtime import pick
from app.eta.scheduler_registry import eta_scheduler
from app.models import JobWorkload
from app.services.jobs import enqueue_job
from app.services.seo_gate import SEO_COOK_TICK_DEFAULT

logger = logging.getLogger(__name__)


def _enqueue_if_cook_enabled(name: str, payload: dict) -> None:
    with SessionLocal() as db:
        from app.repositories import seo as seo_repo

        settings = seo_repo.get_settings(db)
        pick(
            bool(settings.get("cook_enabled")),
            lambda: enqueue_job(db, name=name, workload=JobWorkload.io, payload=payload),
            lambda: None,
        )


@eta_scheduler(every_minutes=60, key="seo.cook_batch")
def run_seo_cook_batch() -> None:
    """Enqueue cook work: heavy LLM stays in the ETA handler."""
    _enqueue_if_cook_enabled("seo.cook_batch", {"limit": SEO_COOK_TICK_DEFAULT})


@eta_scheduler(every_minutes=360, key="seo.ensure_sd_daily")
def run_seo_ensure_sd_daily() -> None:
    _enqueue_if_cook_enabled("seo.ensure_sd_daily", {})
