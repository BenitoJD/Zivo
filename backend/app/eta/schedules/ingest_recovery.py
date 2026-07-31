"""Recover documents stuck in indexing with no active ingest jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.eta.stale_jobs import ACTIVE_JOB_LIVENESS_SQL, stale_running_cutoff
from app.models import Document
from app.services.rag_window import maybe_recover_stuck_indexing

logger = logging.getLogger(__name__)

_STUCK_INDEXING_GRACE_MINUTES = 2


def _recover_stuck_indexing_documents() -> int:
    recovered = 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STUCK_INDEXING_GRACE_MINUTES)
    with SessionLocal() as db:
        doc_ids = db.execute(
            text(
                f"""
                SELECT d.id
                FROM qb.documents d
                WHERE d.status = 'indexing'
                  AND d.created_at < :cutoff
                  AND NOT EXISTS (
                    SELECT 1 FROM qb.jobs j
                    WHERE j.payload->>'document_id' = d.id::text
                      AND {ACTIVE_JOB_LIVENESS_SQL}
                  )
                ORDER BY d.created_at ASC
                LIMIT 50
                """
            ),
            {"cutoff": cutoff, "stale_cutoff": stale_running_cutoff()},
        ).scalars().all()
        for doc_id in doc_ids:
            doc = db.get(Document, doc_id)
            if not doc:
                continue
            try:
                maybe_recover_stuck_indexing(db, doc)
                recovered += 1
                logger.info("ingest recovery re-queued work for document %s", doc_id)
            except Exception:
                logger.exception("ingest recovery failed for document %s", doc_id)
    return recovered


@eta_scheduler(every_minutes=5, key="ingest.recover_stuck")
def run_ingest_recover() -> None:
    n = _recover_stuck_indexing_documents()
    if n:
        logger.info("ingest recovery touched %s stuck documents", n)
