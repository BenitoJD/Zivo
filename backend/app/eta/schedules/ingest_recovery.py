"""Recover documents stuck in indexing with no active ingest jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.engine_runtime import pick
from app.eta.scheduler_registry import eta_scheduler
from app.eta.stale_jobs import (
    ACTIVE_JOB_LIVENESS_SQL,
    stale_queued_cutoff,
    stale_running_cutoff,
)
from app.models import Document
from app.services.rag_window import maybe_recover_stuck_indexing
from app.services.session_design import plan_ingest_recovery_schedule

logger = logging.getLogger(__name__)


def _recover_one(db, doc_id, recover_fn, label: str) -> int:
    doc = db.get(Document, doc_id)

    def _recover() -> int:
        try:
            recover_fn(db, doc)
            logger.info("ingest recovery %s for document %s", label, doc_id)
            return 1
        except Exception:
            logger.exception("ingest recovery failed for document %s", doc_id)
            return 0

    return pick(doc is None, lambda: 0, _recover)


def _recover_stuck_indexing_documents() -> int:
    schedule = plan_ingest_recovery_schedule()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=schedule.indexing_grace_minutes)
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
                LIMIT {int(schedule.indexing_batch)}
                """
            ),
            {
                "cutoff": cutoff,
                "stale_cutoff": stale_running_cutoff(),
                "queued_cutoff": stale_queued_cutoff(),
            },
        ).scalars().all()
        return sum(
            map(
                lambda doc_id: _recover_one(
                    db, doc_id, maybe_recover_stuck_indexing, "re-queued work"
                ),
                doc_ids,
            )
        )


def _recover_stuck_prepping_documents() -> int:
    """Recover background-prep documents stranded in the cooking phase.

    A background-prep doc reaches ``status='prepping'`` once indexing finishes
    and the cook (triage + generate.questions) begins. The cook chain is only
    re-driven by job-success callbacks (``tick_background_cook``); if that job
    dies, the doc sits frozen at its last ``prep_progress`` forever, which the
    sources sidebar renders as "Prepping X%" indefinitely. The per-artifact GET
    recovers it on open, but the sources list never does.

    This finds prepping background-prep docs with NO active ingest OR cook job
    and re-ticks the cook chain. The liveness check spans both ingest-stage and
    cook-stage job names so a genuinely-running cook is never double-enqueued.
    """
    from app.services.background_prep import maybe_recover_stuck_background_prep

    schedule = plan_ingest_recovery_schedule()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=schedule.prepping_grace_minutes)
    with SessionLocal() as db:
        doc_ids = db.execute(
            text(
                f"""
                SELECT d.id
                FROM qb.documents d
                WHERE d.status = 'prepping'
                  AND d.meta->>'prep_mode' = 'background'
                  AND d.meta->>'prep_complete' IS DISTINCT FROM 'true'
                  AND d.created_at < :cutoff
                  AND NOT EXISTS (
                    SELECT 1 FROM qb.jobs j
                    WHERE j.payload->>'document_id' = d.id::text
                      AND j.name = ANY(CAST(:names AS text[]))
                      AND {ACTIVE_JOB_LIVENESS_SQL}
                  )
                ORDER BY d.created_at ASC
                LIMIT {int(schedule.prepping_batch)}
                """
            ),
            {
                "cutoff": cutoff,
                "stale_cutoff": stale_running_cutoff(),
                "queued_cutoff": stale_queued_cutoff(),
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
        return sum(
            map(
                lambda doc_id: _recover_one(
                    db,
                    doc_id,
                    maybe_recover_stuck_background_prep,
                    "re-ticked background prep",
                ),
                doc_ids,
            )
        )


@eta_scheduler(every_minutes=5, key="ingest.recover_stuck")
def run_ingest_recover() -> None:
    n = _recover_stuck_indexing_documents()
    n += _recover_stuck_prepping_documents()
    pick(
        bool(n),
        lambda: logger.info("ingest recovery touched %s stuck documents", n),
        lambda: None,
    )
