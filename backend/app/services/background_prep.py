"""Background prep: index and cook the full selected page range before study."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.engine_runtime import apply, choose, pick
from app.models import Document, Job, JobPriority, JobWorkload
from app.repositories import workspace as workspace_repo
from app.services.question_pool import (
    FIRST_QUESTION_BATCH_SIZE,
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
from app.services.session_design import (
    PREP_MODE_BACKGROUND,
    PREP_MODE_NOW,  # noqa: F401  (re-export for callers)
    PREP_PHASE_COOKING,
    PREP_PHASE_INDEXING,
    evaluate_background_cook_tick,
    evaluate_background_prep_active,
    evaluate_capped_prep_promote,
    evaluate_page_prep_ready,
    evaluate_prep_progress,
    evaluate_prep_short_circuit,
    plan_refill_batch,
)

_PREP_PHASE_INDEXING = PREP_PHASE_INDEXING
_PREP_PHASE_COOKING = PREP_PHASE_COOKING
_PREP_PHASE_COMPLETE = "complete"


def is_background_prep(doc: Document | None) -> bool:
    meta = pick(bool(doc), lambda: (doc.meta or {}), lambda: {})
    return evaluate_background_prep_active(
        has_doc=bool(doc),
        prep_mode=meta.get("prep_mode"),
        prep_complete=bool(meta.get("prep_complete")),
    )


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
    budget = choose(bool(coverage), get_question_budget(doc, page), 0)
    generated = choose(bool(coverage), count_assertions_on_page(db, doc.id, page), 0)
    return evaluate_page_prep_ready(
        has_coverage=bool(coverage),
        non_content=bool((coverage or {}).get("non_content")),
        budget=budget,
        generated=generated,
        coverage_complete=choose(bool(coverage), is_coverage_complete(doc, page), False),
    )


def all_study_pages_indexed(db: Session, doc: Document) -> bool:
    study = study_pages_for_doc(doc)
    return pick(
        not study,
        lambda: False,
        lambda: all(page in pages_ready_for_document(db, doc.id, doc) for page in study),
    )


def compute_prep_progress(db: Session, doc: Document) -> dict[str, Any]:
    study = study_pages_for_doc(doc)
    index_pct = choose(bool(study), rag_window_index_progress(db, doc.id, study), 0)
    cook_done = choose(
        bool(study),
        sum(map(lambda page: int(page_learn_prep_ready(db, doc, page)), study)),
        0,
    )
    cook_pct = choose(bool(study), int(100 * cook_done / len(study)), 0)
    verdict = evaluate_prep_progress(
        index_pct=index_pct,
        cook_pct=cook_pct,
        phase=prep_phase(doc),
        has_study_pages=bool(study),
    )
    return {
        "phase": verdict.phase,
        "index_pct": verdict.index_pct,
        "cook_pct": verdict.cook_pct,
        "overall_pct": verdict.overall_pct,
    }


def is_prep_complete(db: Session, doc: Document) -> bool:
    meta = doc.meta or {}
    return pick(
        evaluate_prep_short_circuit(
            prep_complete=bool(meta.get("prep_complete")),
            prep_mode=meta.get("prep_mode"),
        ),
        lambda: True,
        lambda: _prep_pages_complete(db, doc),
    )


def _prep_pages_complete(db: Session, doc: Document) -> bool:
    study = study_pages_for_doc(doc)
    return bool(study) and all_study_pages_indexed(db, doc) and all(
        page_learn_prep_ready(db, doc, page) for page in study
    )


def apply_prep_meta(
    db: Session,
    doc: Document,
    *,
    prep_mode: str,
) -> None:
    meta = dict(doc.meta or {})
    meta["prep_mode"] = prep_mode
    meta.pop("prep_complete", None)
    pick(
        prep_mode == PREP_MODE_BACKGROUND,
        lambda: meta.__setitem__("prep_phase", _PREP_PHASE_INDEXING),
        lambda: meta.pop("prep_phase", None),
    )
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
    pick(not doc.account_id, lambda: None, lambda: _upsert_workspace(db, doc, status, pool_available_count))


def _upsert_workspace(
    db: Session,
    doc: Document,
    status: str,
    pool_available_count: int | None,
) -> None:
    captured = doc.artifact_captured_at or doc.created_at
    fields: dict[str, Any] = {"status": status}
    pick(
        pool_available_count is not None,
        lambda: fields.__setitem__("pool_available_count", pool_available_count),
        lambda: None,
    )
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
    pick(not pages, lambda: None, lambda: _enqueue_ingest_batch(db, document_id, pages))


def _enqueue_ingest_batch(db: Session, document_id: uuid.UUID, pages: list[int]) -> None:
    from app.services.jobs import batch_enqueue_jobs
    from app.services.session_design import plan_newspaper_ingest_batch

    batch = list(plan_newspaper_ingest_batch(pages))
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
    return pick(not study, lambda: [], lambda: _start_ingest(db, doc, study, current_page))


def _start_ingest(
    db: Session, doc: Document, study: list[int], current_page: int | None
) -> list[int]:
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
    pick(bool(missing), lambda: _enqueue_missing_ingest_pages(db, doc.id, missing), lambda: None)
    return missing


def on_page_ingested_for_prep(db: Session, document_id: uuid.UUID) -> None:
    doc = db.get(Document, document_id)
    pick(
        not doc or not is_background_prep(doc),
        lambda: None,
        lambda: _after_page_ingest(db, document_id, doc),
    )


def _after_page_ingest(db: Session, document_id: uuid.UUID, doc: Document) -> None:
    study = study_pages_for_doc(doc)
    doc.index_progress = rag_window_index_progress(db, document_id, study)
    db.add(doc)
    db.commit()
    pick(all_study_pages_indexed(db, doc), lambda: _transition_to_cooking(db, doc), lambda: None)


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


def _needs_triage(db: Session, document_id: uuid.UUID, doc: Document, page: int, ready: set[int]) -> bool:
    from app.services.question_pool_jobs import _has_active_triage_job_for_page

    return (
        page in ready
        and not get_page_coverage(doc, page)
        and not _has_active_triage_job_for_page(db, document_id, page)
    )


def _next_page_needing_triage(db: Session, document_id: uuid.UUID, doc: Document) -> int | None:
    ready = pages_ready_for_document(db, document_id, doc)
    return next(
        filter(lambda page: _needs_triage(db, document_id, doc, page, ready), study_pages_for_doc(doc)),
        None,
    )


def _needs_cook(db: Session, document_id: uuid.UUID, doc: Document, page: int) -> bool:
    from app.services.question_pool_jobs import _has_active_generate_job_for_page

    return (
        bool(get_page_coverage(doc, page))
        and not page_learn_prep_ready(db, doc, page)
        and not _has_active_generate_job_for_page(db, document_id, page)
    )


def _next_page_needing_cook(db: Session, document_id: uuid.UUID, doc: Document) -> int | None:
    return next(
        filter(lambda page: _needs_cook(db, document_id, doc, page), study_pages_for_doc(doc)),
        None,
    )


def tick_background_cook(db: Session, document_id: uuid.UUID) -> Job | None:
    doc = db.get(Document, document_id)
    return pick(
        not doc or not is_background_prep(doc),
        lambda: None,
        lambda: _tick_prep(db, document_id, doc),
    )


def _tick_prep(db: Session, document_id: uuid.UUID, doc: Document) -> Job | None:
    from app.services.question_pool_jobs import BACKGROUND_PREP_MAX_QUESTIONS

    phase = prep_phase(doc)
    indexed = all_study_pages_indexed(db, doc)
    return pick(
        phase == _PREP_PHASE_INDEXING and not indexed,
        lambda: _tick_indexing(db, document_id, doc, BACKGROUND_PREP_MAX_QUESTIONS),
        lambda: pick(
            phase == _PREP_PHASE_INDEXING,
            lambda: _tick_after_index_transition(db, document_id),
            lambda: _tick_cooking(db, document_id, doc, BACKGROUND_PREP_MAX_QUESTIONS),
        ),
    )


def _tick_indexing(
    db: Session, document_id: uuid.UUID, doc: Document, max_questions: int
) -> None:
    missing = sync_rag_window(db, document_id, study_pages_for_doc(doc))
    verdict = evaluate_background_cook_tick(
        phase=_PREP_PHASE_INDEXING,
        all_indexed=False,
        has_ingest_missing=bool(missing),
        triage_page=None,
        cook_page=None,
        total_generated=0,
        max_questions=max_questions,
        remaining_on_page=None,
    )
    pick(verdict.action == "ingest", lambda: _enqueue_missing_ingest_pages(db, document_id, missing), lambda: None)
    return None


def _tick_after_index_transition(db: Session, document_id: uuid.UUID) -> Job | None:
    doc = db.get(Document, document_id)
    pick(bool(doc), lambda: _transition_to_cooking(db, doc), lambda: None)
    doc = db.get(Document, document_id)
    return pick(not doc, lambda: None, lambda: _tick_cooking_after_transition(db, document_id, doc))


def _tick_cooking_after_transition(db: Session, document_id: uuid.UUID, doc: Document) -> Job | None:
    from app.services.question_pool_jobs import BACKGROUND_PREP_MAX_QUESTIONS

    return _tick_cooking(db, document_id, doc, BACKGROUND_PREP_MAX_QUESTIONS)


def _tick_cooking(
    db: Session, document_id: uuid.UUID, doc: Document, max_questions: int
) -> Job | None:
    from app.services.question_pool_jobs import enqueue_page_triage

    ingest_missing = sync_rag_window(db, document_id, study_pages_for_doc(doc))
    triage_page = pick(
        bool(ingest_missing),
        lambda: None,
        lambda: _next_page_needing_triage(db, document_id, doc),
    )
    cook_page = pick(
        bool(ingest_missing) or triage_page is not None,
        lambda: None,
        lambda: _next_page_needing_cook(db, document_id, doc),
    )
    remaining, total_generated = pick(
        cook_page is not None,
        lambda: (
            get_question_budget(doc, cook_page) - count_assertions_on_page(db, document_id, cook_page),
            count_assertions_for_document(db, document_id),
        ),
        lambda: (None, 0),
    )

    verdict = evaluate_background_cook_tick(
        phase=_PREP_PHASE_COOKING,
        all_indexed=True,
        has_ingest_missing=bool(ingest_missing),
        triage_page=triage_page,
        cook_page=cook_page,
        total_generated=total_generated,
        max_questions=max_questions,
        remaining_on_page=remaining,
    )
    return apply(
        verdict.action,
        {
            "ingest": lambda: (_enqueue_missing_ingest_pages(db, document_id, ingest_missing), None)[1],
            "triage": lambda: enqueue_page_triage(db, doc, page=verdict.page, precompute=True),
            "complete": lambda: (maybe_complete_prep(db, doc), None)[1],
            "complete_cap": lambda: _complete_cap(db, doc),
            "cook": lambda: _cook_page(db, document_id, doc, verdict.page, remaining),
            "idle": lambda: None,
            "transition_cook": lambda: None,
        },
    )


def _complete_cap(db: Session, doc: Document) -> None:
    meta = dict(doc.meta or {})
    meta["prep_complete"] = True
    doc.meta = meta
    flag_modified(doc, "meta")
    ensure_capped_prep_ready(db, doc)
    return None


def _cook_page(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    page: int | None,
    remaining: int | None,
) -> Job | None:
    return pick(
        page is None,
        lambda: None,
        lambda: _enqueue_cook(db, document_id, doc, page, remaining),
    )


def _enqueue_cook(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    page: int,
    remaining: int | None,
) -> Job:
    from app.services.question_pool_jobs import enqueue_page_batch

    generated = count_assertions_on_page(db, document_id, page)
    budget = get_question_budget(doc, page)
    capped = min(max(0, remaining or 0), budget)
    batch = plan_refill_batch(remaining=capped)
    job = enqueue_page_batch(
        db,
        doc,
        page=page,
        batch_size=batch,
        start_sequence=generated,
    )
    maybe_complete_prep(db, doc)
    return job


def on_background_triage_completed(
    db: Session, document_id: uuid.UUID, *, page: int
) -> Job | None:
    doc = db.get(Document, document_id)
    return pick(
        not doc or not is_background_prep(doc),
        lambda: None,
        lambda: _after_triage(db, document_id, doc, page),
    )


def _after_triage(db: Session, document_id: uuid.UUID, doc: Document, page: int) -> Job | None:
    from app.services.session_design import evaluate_background_first_batch

    budget = get_question_budget(doc, page)
    generated = count_assertions_on_page(db, document_id, page)
    batch = evaluate_background_first_batch(
        budget=budget,
        generated=generated,
        first_batch=FIRST_QUESTION_BATCH_SIZE,
    )
    return pick(
        batch > 0,
        lambda: _enqueue_first_then_tick(db, document_id, doc, page, batch),
        lambda: tick_background_cook(db, document_id),
    )


def _enqueue_first_then_tick(
    db: Session, document_id: uuid.UUID, doc: Document, page: int, batch: int
) -> Job:
    from app.services.question_pool_jobs import enqueue_page_batch

    job = enqueue_page_batch(
        db,
        doc,
        page=page,
        batch_size=batch,
        start_sequence=0,
    )
    tick_background_cook(db, document_id)
    return job


def maybe_complete_prep(db: Session, doc: Document) -> bool:
    return pick(
        not is_background_prep(doc),
        lambda: False,
        lambda: pick(
            not is_prep_complete(db, doc),
            lambda: _mark_incomplete_progress(db, doc),
            lambda: _mark_prep_ready(db, doc),
        ),
    )


def _mark_incomplete_progress(db: Session, doc: Document) -> bool:
    progress = compute_prep_progress(db, doc)
    doc.index_progress = int(progress["overall_pct"])
    db.add(doc)
    db.commit()
    return False


def _mark_prep_ready(db: Session, doc: Document) -> bool:
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
    return pick(
        not evaluate_capped_prep_promote(
            prep_complete=bool(meta.get("prep_complete")), status=doc.status
        ),
        lambda: False,
        lambda: _promote_capped(db, doc, meta),
    )


def _promote_capped(db: Session, doc: Document, meta: dict[str, Any]) -> bool:
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
    pick(
        not is_background_prep(doc),
        lambda: None,
        lambda: _recover_prep(db, doc),
    )


def _recover_prep(db: Session, doc: Document) -> None:
    from app.services.rag_window import has_active_ingest_jobs

    pick(has_active_ingest_jobs(db, doc.id), lambda: None, lambda: tick_background_cook(db, doc.id))
