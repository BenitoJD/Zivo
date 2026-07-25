"""ETA schedules for newspaper retention + light reconcile enqueue."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.services.jobs import enqueue_rag_window
from app.services.newspaper import purge_expired_editions

logger = logging.getLogger(__name__)

_STUCK_INDEXING_TIMEOUT_MINUTES = 30


def _recover_stuck_editions() -> int:
    """Re-enqueue rag_window for editions that got stuck in indexing/pending.

    An edition can get stuck when its ingest.rag_window job is lost (never
    enqueued, cancelled, or cleaned up before completion).  This recovery
    finds editions whose document still exists but has no active ingest
    pipeline and re-schedules the rag_window job.
    """
    recovered = 0
    with SessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=_STUCK_INDEXING_TIMEOUT_MINUTES
        )
        stuck = db.execute(
            text(
                """
                SELECT e.id, e.document_id, e.paper_slug, e.edition_date
                FROM qb.newspaper_edition e
                JOIN qb.documents d ON d.id = e.document_id
                WHERE e.status <> 'purged'
                  AND d.status NOT IN ('ready', 'retracted')
                  AND e.updated_at < :cutoff
                  AND NOT EXISTS (
                    SELECT 1 FROM qb.jobs j
                    WHERE j.payload->>'document_id' = e.document_id::text
                      AND j.status IN ('queued', 'running')
                  )
                ORDER BY e.edition_date DESC
                """
            ),
            {"cutoff": cutoff},
        ).mappings().all()

        for row in stuck:
            try:
                enqueue_rag_window(
                    db, row["document_id"], current_page=1
                )
                recovered += 1
                logger.info(
                    "newspaper recovery enqueued rag_window for %s %s",
                    row["paper_slug"],
                    row["edition_date"],
                )
            except Exception:
                logger.exception(
                    "newspaper recovery failed for edition %s", row["id"]
                )
    return recovered


@eta_scheduler(every_minutes=15, key="newspaper.recover_stuck")
def run_newspaper_recover() -> None:
    n = _recover_stuck_editions()
    if n:
        logger.info("newspaper recovery re-enqueued %s stuck editions", n)


@eta_scheduler(every_minutes=360, key="newspaper.purge_expired")
def run_newspaper_purge() -> None:
    with SessionLocal() as db:
        n = purge_expired_editions(db)
        if n:
            logger.info("newspaper purge removed %s expired editions", n)
