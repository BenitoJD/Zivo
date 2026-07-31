"""Sliding-window RAG for tutor chat — max N pages indexed at a time."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import Document, JobWorkload
from app.eta.stale_jobs import ACTIVE_JOB_LIVENESS_SQL, stale_running_cutoff
from app.services.chunks import indexed_pages_for_document
from app.services.question_pool import get_progress, selected_page_list
from app.services.tutor_retrieval import MAX_RAG_PAGES, plan_rag_window

_INGESTED_PAGES_KEY = "ingested_pages"

_INGEST_JOB_NAMES = (
    "ingest.page",
    "ingest.rag_window",
    "ingest.document",
    "ingest.parse_document",
    "ingest.chunk_pages",
    "ingest.embed_chunks",
    "ingest.fetch_file",
)

_MARK_PAGE_INGESTED_SQL = text(
    """
    UPDATE qb.documents d
    SET meta = jsonb_set(
        COALESCE(d.meta, '{}'::jsonb),
        '{ingested_pages}',
        (
            SELECT COALESCE(jsonb_agg(val ORDER BY val), '[]'::jsonb)
            FROM (
                SELECT DISTINCT val
                FROM (
                    SELECT jsonb_array_elements_text(
                        COALESCE(d.meta->'ingested_pages', '[]'::jsonb)
                    )::int AS val
                    UNION ALL
                    SELECT CAST(:page AS integer) AS val
                ) AS combined
            ) AS distinct_vals
        ),
        true
    )
    WHERE d.id = :document_id
    RETURNING meta
    """
)

_HAS_ACTIVE_INGEST_JOBS_SQL = text(
    f"""
    SELECT 1
    FROM qb.jobs j
    WHERE j.payload->>'document_id' = :document_id
      AND j.name = ANY(CAST(:names AS text[]))
      AND {ACTIVE_JOB_LIVENESS_SQL}
    LIMIT 1
    """
)


def ingested_pages_for_document(doc: Document) -> set[int]:
    raw = (doc.meta or {}).get(_INGESTED_PAGES_KEY) or []
    if not isinstance(raw, list):
        return set()
    return {int(p) for p in raw if int(p) >= 1}


def has_active_ingest_jobs(db: Session, document_id: uuid.UUID) -> bool:
    row = db.execute(
        _HAS_ACTIVE_INGEST_JOBS_SQL,
        {
            "document_id": str(document_id),
            "names": list(_INGEST_JOB_NAMES),
            "stale_cutoff": stale_running_cutoff(),
        },
    ).first()
    return row is not None


def enqueue_missing_page_ingests(
    db: Session, document_id: uuid.UUID, pages: list[int]
) -> None:
    if not pages:
        return
    from app.services.jobs import batch_enqueue_jobs

    batch_enqueue_jobs(
        db,
        [
            {
                "name": "ingest.page",
                "workload": JobWorkload.cpu,
                "payload": {
                    "document_id": str(document_id),
                    "page_number": int(page),
                },
            }
            for page in pages
        ],
        chunk_size=50,
    )


def mark_page_ingested(db: Session, doc: Document, page: int) -> None:
    """Record that ingest.page finished for this page — even when text was empty.

    Empty pages write no chunks, so indexed_pages alone can never include them.
    Without this mark, triage defers forever and the RAG window never becomes ready.

    Uses a single atomic SQL UPDATE so concurrent ingest.page workers do not lose
    each other's pages (lost-update on doc.meta left RAG windows stuck at 50%).
    """
    page = int(page)
    if page < 1:
        return
    if page in ingested_pages_for_document(doc):
        return
    row = db.execute(
        _MARK_PAGE_INGESTED_SQL,
        {"document_id": doc.id, "page": page},
    ).mappings().first()
    if not row:
        return
    doc.meta = dict(row["meta"] or {})
    flag_modified(doc, "meta")
    db.add(doc)


def pages_ready_for_document(
    db: Session, document_id: uuid.UUID, doc: Document | None = None
) -> set[int]:
    """Pages ingest has finished for: embedded chunks ∪ empty-but-ingested."""
    if doc is None:
        doc = db.get(Document, document_id)
    indexed = indexed_pages_for_document(db, document_id)
    if not doc:
        return indexed
    return indexed | ingested_pages_for_document(doc)


def chat_rag_window(
    current_page: int,
    study_pages: list[int],
    *,
    max_pages: int = MAX_RAG_PAGES,
) -> list[int]:
    """Pages to index for chat RAG around the learner's current page.

    Policy lives in Tutor Retrieval Engine (``plan_rag_window``).
    """
    return list(
        plan_rag_window(current_page, study_pages, max_pages=max_pages).pages
    )


def rag_window_meta(pages: list[int]) -> dict[str, Any]:
    ordered = sorted(pages)
    return {
        "pages": ordered,
        "page_start": ordered[0],
        "page_end": ordered[-1],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_rag_window(doc: Document) -> list[int]:
    raw = (doc.meta or {}).get("rag_window") or {}
    pages = raw.get("pages")
    if isinstance(pages, list) and pages:
        return sorted({int(p) for p in pages})
    progress = get_progress(doc)
    current = int(progress.get("current_page") or 1)
    return chat_rag_window(current, selected_page_list(doc))


def save_rag_window(db: Session, doc: Document, pages: list[int]) -> None:
    meta = dict(doc.meta or {})
    meta["rag_window"] = rag_window_meta(pages)
    meta.pop("rag_window_ready", None)
    doc.meta = meta
    flag_modified(doc, "meta")


def sync_rag_window(
    db: Session,
    document_id: uuid.UUID,
    target_pages: list[int],
) -> list[int]:
    """Return study pages in the window that still need ingest.

    Embeddings are retained for all pages once indexed — scrolling back does
    not force a full re-parse/re-embed. Chat retrieval scopes to the active
    window via ``doc.meta['rag_window']`` instead of physical deletion.
    """
    allowed = {int(p) for p in target_pages}
    ready = pages_ready_for_document(db, document_id)
    return sorted(p for p in allowed if p not in ready)


def rag_window_index_progress(db: Session, document_id: uuid.UUID, target_pages: list[int]) -> int:
    if not target_pages:
        return 100
    ready = pages_ready_for_document(db, document_id)
    done = sum(1 for p in target_pages if p in ready)
    return int(100 * done / len(target_pages))


def is_rag_window_ready(db: Session, document_id: uuid.UUID, doc: Document | None = None) -> bool:
    if doc is None:
        doc = db.get(Document, document_id)
    if not doc:
        return False
    meta = doc.meta or {}
    target = get_rag_window(doc)
    # The saved window is only rewritten when an ingest actually runs, so the learner
    # can walk past it (current page 10, window still [4..9]). Judging readiness
    # against that stale target - or against the sticky `rag_window_ready` flag it
    # was saved with - deadlocks the learn loop: page triage defers because the
    # current page has no chunks, while this reports "ready" so ensure_question_pool
    # never enqueues the ingest that would create them, and generation_pending stays
    # true forever (the UI sits at "Planning the quiz"). When the current page has
    # escaped the saved window, re-derive the window the learner needs NOW.
    current = int((get_progress(doc) or {}).get("current_page") or 0)
    if current > 0 and current not in set(target):
        target = chat_rag_window(current, selected_page_list(doc))
    elif meta.get("rag_window_ready"):
        return True
    if not target:
        return True
    ready = pages_ready_for_document(db, document_id, doc)
    return all(p in ready for p in target)


def maybe_recover_stuck_indexing(db: Session, doc: Document) -> None:
    """Re-queue missing RAG-window pages when indexing stalled with no active jobs."""
    if doc.status != "indexing":
        return
    if has_active_ingest_jobs(db, doc.id):
        return
    target = get_rag_window(doc)
    if not target:
        from app.services.jobs import enqueue_rag_window

        enqueue_rag_window(
            db,
            doc.id,
            account_id=doc.account_id,
            current_page=int((get_progress(doc) or {}).get("current_page") or 1),
        )
        db.commit()
        return
    if is_rag_window_ready(db, doc.id, doc):
        refresh_rag_window_status(db, doc.id)
        return
    missing = sync_rag_window(db, doc.id, target)
    if missing:
        enqueue_missing_page_ingests(db, doc.id, missing)
        db.commit()


def refresh_rag_window_status(db: Session, document_id: uuid.UUID) -> bool:
    """Update index_progress and ready flag; return True when window fully indexed."""
    doc = db.get(Document, document_id)
    if not doc:
        return False
    target = get_rag_window(doc)
    pct = rag_window_index_progress(db, document_id, target)
    doc.index_progress = pct
    ready = is_rag_window_ready(db, document_id, doc)
    if not ready:
        missing = sync_rag_window(db, document_id, target)
        if missing and not has_active_ingest_jobs(db, document_id):
            enqueue_missing_page_ingests(db, document_id, missing)
            ready = False
    meta = dict(doc.meta or {})
    if ready:
        doc.status = "ready"
        meta["rag_window_ready"] = True
    doc.meta = meta
    flag_modified(doc, "meta")
    db.commit()
    if ready:
        from app.services.background_prep import is_background_prep
        from app.services.question_generation import enqueue_generate_if_needed

        if not is_background_prep(doc):
            # Newspaper catalog "ready" waits for the first cooked MCQ (see
            # maybe_mark_newspaper_edition_ready) — not merely RAG index complete.
            enqueue_generate_if_needed(db, document_id)
    return ready
