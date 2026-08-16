"""ETA schedules for newspaper retention + light reconcile enqueue."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.engine_runtime import apply, pick
from app.eta.scheduler_registry import eta_scheduler
from app.eta.stale_jobs import (
    ACTIVE_JOB_LIVENESS_SQL,
    stale_queued_cutoff,
    stale_running_cutoff,
)
from app.models import Document, JobWorkload
from app.services.jobs import enqueue_job, enqueue_rag_window
from app.services.newspaper import purge_expired_editions
from app.services.session_design import (
    STUCK_INDEXING_TIMEOUT_MINUTES,
    STUCK_READY_DOC_TIMEOUT_MINUTES,
    plan_newspaper_recovery_batch,
    plan_newspaper_uncooked_followup,
)

logger = logging.getLogger(__name__)


def _recover_stuck_editions() -> int:
    """Re-enqueue rag_window for editions that got stuck in indexing/pending.

    An edition can get stuck when its ingest.rag_window job is lost (never
    enqueued, cancelled, or cleaned up before completion). This recovery
    finds editions whose document still exists but has no active ingest
    pipeline and re-schedules the rag_window job.
    """
    recovered = 0
    with SessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=STUCK_INDEXING_TIMEOUT_MINUTES
        )
        stuck = db.execute(
            text(
                f"""
                SELECT e.id, e.document_id, e.paper_slug, e.edition_date
                FROM qb.newspaper_edition e
                JOIN qb.documents d ON d.id = e.document_id
                WHERE e.status <> 'purged'
                  AND d.status NOT IN ('ready', 'retracted')
                  AND e.updated_at < :cutoff
                  AND NOT EXISTS (
                    SELECT 1 FROM qb.jobs j
                    WHERE j.payload->>'document_id' = e.document_id::text
                      AND {ACTIVE_JOB_LIVENESS_SQL}
                  )
                ORDER BY e.edition_date DESC
                """
            ),
            {
                "cutoff": cutoff,
                "stale_cutoff": stale_running_cutoff(),
                "queued_cutoff": stale_queued_cutoff(),
            },
        ).mappings().all()

        def _one(row) -> int:
            try:
                from app.models import Document
                from app.services.rag_window import maybe_recover_stuck_indexing

                doc = db.get(Document, row["document_id"])
                pick(
                    doc is not None,
                    lambda: maybe_recover_stuck_indexing(db, doc),
                    lambda: enqueue_rag_window(db, row["document_id"], current_page=1),
                )
                logger.info(
                    "newspaper recovery enqueued rag_window for %s %s",
                    row["paper_slug"],
                    row["edition_date"],
                )
                return 1
            except Exception:
                logger.exception("newspaper recovery failed for edition %s", row["id"])
                return 0

        recovered = sum(map(_one, stuck))
    return recovered


@eta_scheduler(every_minutes=15, key="newspaper.recover_stuck")
def run_newspaper_recover() -> None:
    n = _recover_stuck_editions()
    n += _recover_editions_doc_ready_uncooked()
    pick(
        bool(n),
        lambda: logger.info("newspaper recovery re-enqueued %s stuck editions", n),
        lambda: None,
    )


def _recover_editions_doc_ready_uncooked() -> int:
    """Re-enqueue the MCQ cook for editions whose document is READY but whose
    catalog status is still indexing/pending: the "document ready, edition
    stuck" orphan state.

    Newspapers decouple the two statuses by design (rag_window.py: the document
    flips to ``ready`` once RAG indexing finishes, but the edition stays
    ``indexing`` until the first MCQ lands via ``newspaper_learn_ready``). If
    the ``generate.questions`` cook died, exhausted retries (``max_attempts``),
    or was cancelled, and there is no scheduler that enqueues the cook, the
    edition is stranded forever, which the catalog renders as "Preparing".

    This catches that exact state: a ready document with an indexing edition and
    NO active ``generate.questions`` job, then re-enqueues the page-1 batch
    directly (bypassing ``enqueue_initial_pool``'s ``question_pool_initialized``
    gate, which would have trapped the original failed cook). Idempotent: the
    ``NOT EXISTS`` active-job guard prevents duplicate enqueues.
    """
    recovered = 0
    with SessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=STUCK_READY_DOC_TIMEOUT_MINUTES
        )
        stuck = db.execute(
            text(
                f"""
                SELECT e.id, e.document_id, e.paper_slug, e.edition_date
                FROM qb.newspaper_edition e
                JOIN qb.documents d ON d.id = e.document_id
                WHERE e.status IN ('indexing', 'pending')
                  AND d.status = 'ready'
                  AND e.updated_at < :cutoff
                  AND NOT EXISTS (
                    SELECT 1 FROM qb.jobs j
                    WHERE j.payload->>'document_id' = e.document_id::text
                      AND j.name = 'generate.questions'
                      AND {ACTIVE_JOB_LIVENESS_SQL}
                  )
                ORDER BY e.edition_date DESC
                LIMIT :lim
                """
            ),
            {
                "cutoff": cutoff,
                "stale_cutoff": stale_running_cutoff(),
                "queued_cutoff": stale_queued_cutoff(),
                "lim": plan_newspaper_recovery_batch(),
            },
        ).mappings().all()

        def _one(row) -> int:
            try:
                from app.services.question_pool import page_range_bounds
                from app.services.question_pool_jobs import _enqueue_first_question_batch

                doc = db.get(Document, row["document_id"])

                def _heal() -> int:
                    page_from, _ = page_range_bounds(doc)
                    job = _enqueue_first_question_batch(db, doc, page=page_from)
                    followup = plan_newspaper_uncooked_followup(cook_enqueued=job is not None)

                    def _wait() -> int:
                        db.commit()
                        logger.info(
                            "newspaper recovery re-enqueued MCQ cook for %s %s (doc ready, edition stuck)",
                            row["paper_slug"],
                            row["edition_date"],
                        )
                        return 1

                    def _promote() -> int:
                        from app.services.newspaper import maybe_mark_newspaper_edition_ready

                        maybe_mark_newspaper_edition_ready(db, doc)
                        db.commit()
                        logger.info(
                            "newspaper recovery promoted %s %s to ready (no cookable MCQs on page 1)",
                            row["paper_slug"],
                            row["edition_date"],
                        )
                        return 1

                    return apply(followup, {"wait": _wait, "promote_ready": _promote})

                return pick(doc is None, lambda: 0, _heal)
            except Exception:
                logger.exception(
                    "newspaper doc-ready recovery failed for edition %s", row["id"]
                )
                return 0

        recovered = sum(map(_one, stuck))
    return recovered


@eta_scheduler(every_minutes=360, key="newspaper.purge_expired")
def run_newspaper_purge() -> None:
    with SessionLocal() as db:
        n = purge_expired_editions(db)
        pick(
            bool(n),
            lambda: logger.info("newspaper purge removed %s expired editions", n),
            lambda: None,
        )


def _backfill_edition_digests() -> int:
    """Enqueue digest cook for ready editions that never published a blog."""
    from app.repositories import seo as seo_repo
    from app.services.seo_gate import plan_seo_digest_backfill_batch

    with SessionLocal() as db:
        settings = seo_repo.get_settings(db)

        def _skip() -> int:
            return 0

        def _enqueue_rows() -> int:
            rows = db.execute(
                text(
                    """
                    SELECT e.id, e.paper_slug, e.edition_date
                    FROM qb.newspaper_edition e
                    WHERE e.status = 'ready'
                      AND e.blog_post_id IS NULL
                      AND e.blog_status IN ('none', 'skipped', 'failed')
                    ORDER BY e.edition_date DESC
                    LIMIT :lim
                    """
                ),
                {"lim": plan_seo_digest_backfill_batch()},
            ).mappings().all()

            def _maybe(row) -> int:
                source_key = f"{row['paper_slug']}:{row['edition_date'].isoformat()}"

                def _enqueue() -> int:
                    from app.repositories import newspaper as newspaper_repo

                    newspaper_repo.reset_edition_blog_for_retry(db, row["id"])
                    enqueue_job(
                        db,
                        name="seo.cook_edition_digest",
                        workload=JobWorkload.io,
                        payload={"edition_id": str(row["id"])},
                    )
                    return 1

                return pick(
                    seo_repo.edition_digest_may_enqueue(db, "newspaper_edition", source_key),
                    _enqueue,
                    lambda: 0,
                )

            n = sum(map(_maybe, rows))
            pick(bool(n), db.commit, lambda: None)
            return n

        return pick(not settings.get("cook_enabled"), _skip, _enqueue_rows)


@eta_scheduler(every_minutes=60, key="newspaper.backfill_edition_blogs")
def run_newspaper_backfill_blogs() -> None:
    from app.repositories import newspaper as newspaper_repo

    repaired = 0
    with SessionLocal() as db:
        repaired = newspaper_repo.repair_edition_blog_links(db)
    pick(
        bool(repaired),
        lambda: logger.info("newspaper repaired %s edition blog links", repaired),
        lambda: None,
    )
    n = _backfill_edition_digests()
    pick(
        bool(n),
        lambda: logger.info("newspaper backfill enqueued %s edition digests", n),
        lambda: None,
    )
