"""ETA schedules for newspaper retention + light reconcile enqueue."""

from __future__ import annotations

import logging

from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.services.newspaper import purge_expired_editions

logger = logging.getLogger(__name__)


@eta_scheduler(every_minutes=360, key="newspaper.purge_expired")
def run_newspaper_purge() -> None:
    with SessionLocal() as db:
        n = purge_expired_editions(db)
        if n:
            logger.info("newspaper purge removed %s expired editions", n)
