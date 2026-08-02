"""Background prep: index and cook the full selected page range before study."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import Document, Job, JobPriority, JobWorkload
from app.repositories import workspace as workspace_repo
from app.services.question_pool import (
    FIRST_QUESTION_BATCH_SIZE,
    REFILL_BATCH_SIZE,
    count_assertions_on_page,
    get_page_coverage,
    get_progress,
    get_question_budget,
    is_coverage_complete,
    selected_page_list,
)
from app.services.rag_window import (
    pages_ready_for_document,
    rag_window_index_progress,
    save_rag_window,
    sync_rag_window,
)

PREP_MODE_NOW = "now"
PREP_MODE_BACKGROUND = "background"

_PREP_PHASE_INDEXING = "indexing"
_PREP_PHASE_COOKING = "cooking"
_PREP_PHASE_COMPLETE = "complete"

_INGEST_BATCH_SIZE = 8


def is_background_prep(doc: Document | None) -> bool:
    if not doc:
        return False
    meta = doc.meta or {}
    return meta.get("prep_mode") == PREP_MODE_BACKGROUND and not meta.get("prep_complete")


def count_assertions_for_document(db: Session, document_id: uuid.UUID) -> int:
    """Total active MCQ assertions cooked for a document (any page)."""
    from sqlalchemy import text

    return int(
        db.execute(
            text(
                """
                SELECT count(*) FROM intel.assertion
                WHERE payload->>'artifact_id' = :aid
                  AND status = 'active'
                """
            ),
            {"aid": str(document_id)},
        ).scalar()
        or 0
    )


def prep_phase(doc: Document) -> str:
    meta = doc.meta or {}
    return str(meta.get("prep_phase") or _PREP_PHASE_INDEXING)


def study_pages_for_doc(doc: Document) -> list[int]:
    return selected_page_list(doc)


def page_learn_prep_ready(db: Session, doc: Document, page: int) -> bool:
    coverage = get_page_coverage(doc, page)
    if not coverage:
        return False
    if coverage.get("non_content"):
        return True
    budget = get_question_budget(doc, page)
    if budget <= 0:
        return True
    generated = count_assertions_on_page(db, doc.id, page)
    return generated >= budget or is_coverage_complete(doc, page)


def all_study_pages_indexed(db: Session, doc: Document) -> bool:
    study = study_pages_for_doc(doc)
    if not study:
        return False
    ready = pages_ready_for_document(db, doc.id, doc)
    return all(page in ready for page in study)


def compute_prep_progress(db: Session, doc: Document) -> dict[str, Any]:
    study = study_pages_for_doc(doc)
    if not study:
        return {
            "phase": _PREP_PHASE_INDEXING,
            "index_pct": 0,
            "cook_pct": 0,
            "overall_pct": 0,
        }

    index_pct = rag_window_index_progress(db, doc.id, study)
    cook_done = sum(1 for page in study if page_learn_prep_ready(db, doc, page))
    cook_pct = int(100 * cook_done / len(study))
    phase = prep_phase(doc)
    if phase == _PREP_PHASE_INDEXING and index_pct >= 100:
        phase = _PREP_PHASE_COOKING
    if phase == _PREP_PHASE_INDEXING:
        overall_pct = index_pct
    else:
        overall_pct = int(index_pct * 0.35 + cook_pct * 0.65)
    return {
        "phase": phase,
        "index_pct": index_pct,
        "cook_pct": cook_pct,
        "overall_pct": min(100, overall_pct),
    }


def is_prep_complete(db: Session, doc: Document) -> bool:
    meta = doc.meta or {}
    if meta.get("prep_complete"):
        return True
    if meta.get("prep_mode") != PREP_MODE_BACKGROUND:
        return True
    study = study_pages_for_doc(doc)
    if not study:
        return False
    if not all_study_pages_indexed(db, doc):
        return False
    return all(page_learn_prep_ready(db, doc, page) for page in study)


def apply_prep_meta(
    db: Session,
    doc: Document,
    *,
    prep_mode: str,
) -> None:
    meta = dict(doc.meta or {})
    meta["prep_mode"] = prep_mode
    meta.pop("prep_complete", None)
    if prep_mode == PREP_MODE_BACKGROUND:
        meta["prep_phase"] = _PREP_PHASE_INDEXING
    else:
        meta.pop("prep_phase", None)
    doc.meta = meta
    flag_modified(doc, "meta")
    db.add(doc)


def clear_prep_meta(meta: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(meta)
    cleaned.pop("prep_mode", None)
    cleaned.pop("prep_phase", None)
    cleaned.pop("prep_complete", None)
    return cleaned


def _update_workspace(
    db: Session,
    doc: Document,
    *,
    status: str,
    pool_available_count: int | None = None,
) -> None:
    if not doc.account_id:
        return
    captured = doc.artifact_captured_at or doc.created_at
    fields: dict[str, Any] = {"status": status}
    if pool_available_count is not None:
        fields["pool_available_count"] = pool_available_count
    workspace_repo.upsert_workspace(
        db,
        account_id=doc.account_id,
        artifact_id=doc.id,
        artifact_captured_at=captured,
        **fields,
    )


def _enqueue_missing_ingest_pages(
    db: Session, document_id: uuid.UUID, pages: list[int]
) -> None:
    if not pages:
        return
    from app.services.jobs import batch_enqueue_jobs

    batch = pages[:_INGEST_BATCH_SIZE]
    batch_enqueue_jobs(
        db,
        [
            {
                "name": "ingest.page",
                "workload": JobWorkload.cpu,
                "priority": JobPriority.LOW,
                "payload": {
                    "document_id": str(document_id),
                    "page_number": page,
                },
            }
            for page in batch
        ],
    )


def start_full_range_ingest(
    db: Session,
    doc: Document,
    *,
    current_page: int | None = None,
) -> list[int]:
    study = study_pages_for_doc(doc)
    if not study:
        return []
    page_from = current_page or int(study[0])
    progress = get_progress(doc)
    progress["current_page"] = page_from
    from app.services.document_learn_state import save_progress_row

    save_progress_row(db, doc.id, progress)
    save_rag_window(db, doc, study)
    doc.status = "indexing"
    db.add(doc)
    db.commit()
    missing = sync_rag_window(db, doc.id, study)
    if missing:
        _enqueue_missing_ingest_pages(db, doc.id, missing)
    return missing


def on_page_ingested_for_prep(db: Session, document_id: uuid.UUID) -> None:
    doc = db.get(Document, document_id)
    if not doc or not is_background_prep(doc):
        return
    study = study_pages_for_doc(doc)
    doc.index_progress = rag_window_index_progress(db, document_id, study)
    db.add(doc)
    db.commit()
    if all_study_pages_indexed(db, doc):
        _transition_to_cooking(db, doc)


def _transition_to_cooking(db: Session, doc: Document) -> None:
    meta = dict(doc.meta or {})
    meta["prep_phase"] = _PREP_PHASE_COOKING
    doc.meta = meta
    flag_modified(doc, "meta")
    doc.status = "prepping"
    doc.index_progress = 100
    db.add(doc)
    db.commit()

    from app.services.question_pool_jobs import enqueue_initial_pool_for_background_prep

    enqueue_initial_pool_for_background_prep(db, doc.id)
    tick_background_cook(db, doc.id)
    _update_workspace(db, doc, status="prepping")


def _next_page_needing_triage(db: Session, document_id: uuid.UUID, doc: Document) -> int | None:
    ready = pages_ready_for_document(db, document_id, doc)
    from app.services.question_pool_jobs import _has_active_triage_job_for_page

    for page in study_pages_for_doc(doc):
        if page not in ready:
            continue
        if get_page_coverage(doc, page):
            continue
        if _has_active_triage_job_for_page(db, document_id, page):
            continue
        return page
    return None


def _next_page_needing_cook(db: Session, document_id: uuid.UUID, doc: Document) -> int | None:
    from app.services.question_pool_jobs import _has_active_generate_job_for_page

    for page in study_pages_for_doc(doc):
        if not get_page_coverage(doc, page):
            continue
        if page_learn_prep_ready(db, doc, page):
            continue
        if _has_active_generate_job_for_page(db, document_id, page):
            continue
        return page
    return None


def tick_background_cook(db: Session, document_id: uuid.UUID) -> Job | None:
    doc = db.get(Document, document_id)
    if not doc or not is_background_prep(doc):
        return None
    if prep_phase(doc) == _PREP_PHASE_INDEXING:
        if not all_study_pages_indexed(db, doc):
            missing = sync_rag_window(db, document_id, study_pages_for_doc(doc))
            if missing:
                _enqueue_missing_ingest_pages(db, document_id, missing)
            return None
        _transition_to_cooking(db, doc)
        doc = db.get(Document, document_id)
        if not doc:
            return None

    ingest_missing = sync_rag_window(db, document_id, study_pages_for_doc(doc))
    if ingest_missing:
        _enqueue_missing_ingest_pages(db, document_id, ingest_missing)
        return None

    from app.services.question_pool_jobs import enqueue_page_batch, enqueue_page_triage

    triage_page = _next_page_needing_triage(db, document_id, doc)
    if triage_page is not None:
        return enqueue_page_triage(db, doc, page=triage_page, precompute=True)

    cook_page = _next_page_needing_cook(db, document_id, doc)
    if cook_page is None:
        maybe_complete_prep(db, doc)
        return None

    # Background-prep cap: a very large document (e.g. a 496-page PDF) would
    # otherwise chain one cook batch per page forever, flooding the ETA queue
    # and starving every other document's jobs (newspaper editions sat in
    # "Preparing" behind ~300 queued generate.questions from one upload). Once
    # the document's total cooked questions reach the cap, stop and mark the
    # prep complete — the learner still gets a fully studyable partial pool.
    from app.services.question_pool_jobs import BACKGROUND_PREP_MAX_QUESTIONS

    total_generated = count_assertions_for_document(db, document_id)
    if total_generated >= BACKGROUND_PREP_MAX_QUESTIONS:
        # Cap reached: mark prep complete (partial pool is studyable) and
        # promote the doc to ready so the UI stops showing the indexing modal.
        meta = dict(doc.meta or {})
        meta["prep_complete"] = True
        doc.meta = meta
        flag_modified(doc, "meta")
        ensure_capped_prep_ready(db, doc)
        return None

    generated = count_assertions_on_page(db, document_id, cook_page)
    budget = get_question_budget(doc, cook_page)
    remaining = budget - generated
    if remaining <= 0:
        maybe_complete_prep(db, doc)
        return None
    batch = min(REFILL_BATCH_SIZE, remaining, budget)
    job = enqueue_page_batch(
        db,
        doc,
        page=cook_page,
        batch_size=batch,
        start_sequence=generated,
    )
    maybe_complete_prep(db, doc)
    return job


def on_background_triage_completed(
    db: Session, document_id: uuid.UUID, *, page: int
) -> Job | None:
    doc = db.get(Document, document_id)
    if not doc or not is_background_prep(doc):
        return None
    from app.services.question_pool_jobs import enqueue_page_batch

    budget = get_question_budget(doc, page)
    if budget > 0 and count_assertions_on_page(db, document_id, page) == 0:
        job = enqueue_page_batch(
            db,
            doc,
            page=page,
            batch_size=min(FIRST_QUESTION_BATCH_SIZE, budget),
            start_sequence=0,
        )
        tick_background_cook(db, document_id)
        return job
    return tick_background_cook(db, document_id)


def maybe_complete_prep(db: Session, doc: Document) -> bool:
    if not is_background_prep(doc):
        return False
    if not is_prep_complete(db, doc):
        progress = compute_prep_progress(db, doc)
        doc.index_progress = int(progress["overall_pct"])
        db.add(doc)
        db.commit()
        return False

    meta = dict(doc.meta or {})
    meta["prep_complete"] = True
    meta["prep_phase"] = _PREP_PHASE_COMPLETE
    doc.meta = meta
    flag_modified(doc, "meta")
    doc.status = "ready"
    doc.index_progress = 100
    db.add(doc)
    db.commit()

    from app.services.question_pool import _count_available, get_progress

    progress = get_progress(doc)
    pool = _count_available(db, doc.id, progress)
    _update_workspace(db, doc, status="page_ready", pool_available_count=pool)
    return True


def ensure_capped_prep_ready(db: Session, doc: Document) -> bool:
    """Flip a capped background-prep doc to ready once the cap halts its cook.

    The background-prep cap stops a huge document's cook chain mid-way
    (BACKGROUND_PREP_MAX_QUESTIONS). The doc is left with prep_complete=true
    but status='prepping', so the UI shows the indexing modal forever at a
    stale percent even though the partial pool is studyable. This promotes it
    to ready (partial pool) so the learner can actually open it.
    """
    meta = doc.meta or {}
    if not meta.get("prep_complete") or doc.status == "ready":
        return False
    meta = dict(meta)
    meta["prep_phase"] = _PREP_PHASE_COMPLETE
    doc.meta = meta
    flag_modified(doc, "meta")
    doc.status = "ready"
    doc.index_progress = 100
    db.add(doc)
    db.commit()

    from app.services.question_pool import _count_available, get_progress

    progress = get_progress(doc)
    pool = _count_available(db, doc.id, progress)
    _update_workspace(db, doc, status="page_ready", pool_available_count=pool)
    return True


def maybe_recover_stuck_background_prep(db: Session, doc: Document) -> None:
    if not is_background_prep(doc):
        return
    from app.services.rag_window import has_active_ingest_jobs

    if has_active_ingest_jobs(db, doc.id):
        return
    tick_background_cook(db, doc.id)
