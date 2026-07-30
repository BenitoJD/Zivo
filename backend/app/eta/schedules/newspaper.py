"""ETA schedules for newspaper retention + light reconcile enqueue."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.eta.scheduler_registry import eta_scheduler
from app.eta.stale_jobs import stale_running_cutoff
from app.models import JobWorkload
from app.services.jobs import enqueue_job, enqueue_rag_window
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
                      AND (
                        j.status = 'queued'
                        OR (
                          j.status = 'running'
                          AND j.locked_at IS NOT NULL
                          AND j.locked_at >= :stale_cutoff
                        )
                      )
                  )
                ORDER BY e.edition_date DESC
                """
            ),
            {"cutoff": cutoff, "stale_cutoff": stale_running_cutoff()},
        ).mappings().all()

        for row in stuck:
            try:
                from app.models import Document
                from app.services.rag_window import maybe_recover_stuck_indexing

                doc = db.get(Document, row["document_id"])
                if doc:
                    maybe_recover_stuck_indexing(db, doc)
                else:
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


def _backfill_edition_digests() -> int:
    """Enqueue digest cook for ready editions that never published a blog."""
    from app.repositories import seo as seo_repo

    n = 0
    with SessionLocal() as db:
        settings = seo_repo.get_settings(db)
        if not settings.get("cook_enabled"):
            return 0

        rows = db.execute(
            text(
                """
                SELECT e.id, e.paper_slug, e.edition_date
                FROM qb.newspaper_edition e
                WHERE e.status = 'ready'
                  AND e.blog_post_id IS NULL
                  AND e.blog_status IN ('none', 'skipped', 'failed')
                ORDER BY e.edition_date DESC
                LIMIT 5
                """
            )
        ).mappings().all()

        for row in rows:
            source_key = f"{row['paper_slug']}:{row['edition_date'].isoformat()}"
            if not seo_repo.edition_digest_may_enqueue(
                db, "newspaper_edition", source_key
            ):
                continue
            from app.repositories import newspaper as newspaper_repo

            newspaper_repo.reset_edition_blog_for_retry(db, row["id"])
            enqueue_job(
                db,
                name="seo.cook_edition_digest",
                workload=JobWorkload.io,
                payload={"edition_id": str(row["id"])},
            )
            n += 1

        if n:
            db.commit()
    return n


@eta_scheduler(every_minutes=60, key="newspaper.backfill_edition_blogs")
def run_newspaper_backfill_blogs() -> None:
    from app.repositories import newspaper as newspaper_repo

    repaired = 0
    with SessionLocal() as db:
        repaired = newspaper_repo.repair_edition_blog_links(db)
    if repaired:
        logger.info("newspaper repaired %s edition blog links", repaired)
    n = _backfill_edition_digests()
    if n:
        logger.info("newspaper backfill enqueued %s edition digests", n)
