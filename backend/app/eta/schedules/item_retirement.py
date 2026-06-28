"""Periodic self-improvement: retire MCQ items real learners can't get right."""

from __future__ import annotations

import logging

from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.services.item_retirement import retire_broken_items

logger = logging.getLogger(__name__)


@eta_scheduler(every_minutes=360, key="item_retirement.retire_broken_items")
def run_item_retirement() -> None:
    with SessionLocal() as db:
        try:
            retire_broken_items(db)
        except Exception:
            logger.exception("item retirement sweep failed")
