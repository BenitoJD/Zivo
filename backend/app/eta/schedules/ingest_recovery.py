"""Recover documents stuck in indexing with no active ingest jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.eta.stale_jobs import (
    ACTIVE_JOB_LIVENESS_SQL,
    stale_queued_cutoff,
    stale_running_cutoff,
)
from app.models import Document
from app.services.rag_window import maybe_recover_stuck_indexing

logger = logging.getLogger(__name__)

_STUCK_INDEXING_GRACE_MINUTES = 2
# Background-prep docs in the cooking phase (status='prepping') are re-driven
# only by job-success callbacks (tick_background_cook). If the cook chain dies
# (job exhausted retries / worker pod terminated), the doc is stranded at its
# last prep_progress forever. Generous grace so we don't race a legitimately
# slow cook; the per-artifact GET still recovers on open, but the sources list
# never triggers it — this schedule is the only backstop there.
_STUCK_PREPPING_GRACE_MINUTES = 5


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
            {
                "cutoff": cutoff,
                "stale_cutoff": stale_running_cutoff(),
                "queued_cutoff": stale_queued_cutoff(),
            },
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
    n += _recover_stuck_prepping_documents()
    if n:
        logger.info("ingest recovery touched %s stuck documents", n)


def _recover_stuck_prepping_documents() -> int:
    """Recover background-prep documents stranded in the cooking phase.

    A background-prep doc reaches ``status='prepping'`` once indexing finishes
    and the cook (triage + generate.questions) begins. The cook chain is only
    re-driven by job-success callbacks (``tick_background_cook``); if that job
    dies, the doc sits frozen at its last ``prep_progress`` forever — which the
    sources sidebar renders as "Prepping X%" indefinitely. The per-artifact GET
    recovers it on open, but the sources list never does.

    This finds prepping background-prep docs with NO active ingest OR cook job
    and re-ticks the cook chain. The liveness check spans both ingest-stage and
    cook-stage job names so a genuinely-running cook is never double-enqueued.
    """
    recovered = 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STUCK_PREPPING_GRACE_MINUTES)
    with SessionLocal() as db:
        doc_ids = db.execute(
            text(
                f"""
                SELECT d.id
                FROM qb.documents d
                WHERE d.status = 'prepping'
                  AND d.meta->>'prep_mode' = 'background'
                  AND d.meta->>'prep_complete' IS DISTINCT FROM 'true'
                  AND d.updated_at < :cutoff
                  AND NOT EXISTS (
                    SELECT 1 FROM qb.jobs j
                    WHERE j.payload->>'document_id' = d.id::text
                      AND j.name = ANY(CAST(:names AS text[]))
                      AND {ACTIVE_JOB_LIVENESS_SQL}
                  )
                ORDER BY d.updated_at ASC
                LIMIT 25
                """
            ),
            {
                "cutoff": cutoff,
                "stale_cutoff": stale_running_cutoff(),
                "queued_cutoff": stale_queued_cutoff(),
                # Ingest-stage + cook-stage names: a cook in flight keeps the
                # doc out of recovery so we never enqueue a duplicate batch.
                # Note: triage batches are enqueued as generate.questions with
                # mode='page_triage', so generate.questions covers both.
                "names": [
                    "ingest.page",
                    "ingest.rag_window",
                    "ingest.document",
                    "ingest.full_range",
                    "ingest.parse_document",
                    "ingest.chunk_pages",
                    "ingest.embed_chunks",
                    "ingest.fetch_file",
                    "generate.questions",
                    "learn.transition_prep",
                ],
            },
        ).scalars().all()
        for doc_id in doc_ids:
            doc = db.get(Document, doc_id)
            if not doc:
                continue
            try:
                from app.services.background_prep import maybe_recover_stuck_background_prep

                maybe_recover_stuck_background_prep(db, doc)
                recovered += 1
                logger.info("ingest recovery re-ticked background prep for document %s", doc_id)
            except Exception:
                logger.exception("ingest recovery failed for prepping document %s", doc_id)
    return recovered
