"""Rolling per-page question pool — agent budget, prefetch batches, page advance."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import Document, Job
from app.repositories.intel import create_activity
from app.repositories import workspace as workspace_repo
from app.services.jobs import enqueue_generate, enqueue_rag_window, enqueue_transition_prep

INITIAL_BATCH_SIZE = 5
REFILL_BATCH_SIZE = 5
REFILL_AFTER_ANSWERED = 3
TRANSITION_PREFETCH_RATIO = 0.70
ABSOLUTE_MAX_QUESTIONS_PER_PAGE = 150
# Backward-compatible alias for API consumers
MAX_QUESTIONS_PER_PAGE = ABSOLUTE_MAX_QUESTIONS_PER_PAGE


def default_progress(doc: Document) -> dict[str, Any]:
    study_pages = selected_page_list(doc)
    first_page = study_pages[0] if study_pages else int((doc.meta or {}).get("selected_range", {}).get("from") or 1)
    return {
        "current_page": first_page,
        "answered_ids": [],
        "answered_on_page": 0,
        "generated_on_page": 0,
        "generation_pending": False,
        "page_coverage": {},
        "transition_prep_done": {},
    }


def get_progress(doc: Document) -> dict[str, Any]:
    meta = dict(doc.meta or {})
    progress = meta.get("question_progress")
    if not isinstance(progress, dict):
        progress = default_progress(doc)
    progress.setdefault("current_page", default_progress(doc)["current_page"])
    progress.setdefault("answered_ids", [])
    progress.setdefault("answered_on_page", 0)
    progress.setdefault("generated_on_page", 0)
    progress.setdefault("generation_pending", False)
    progress.setdefault("page_coverage", {})
    progress.setdefault("transition_prep_done", {})
    return progress


def _merge_progress(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in patch.items():
        if key == "page_coverage" and isinstance(value, dict) and isinstance(merged.get(key), dict):
            if not value and merged[key]:
                continue
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def save_progress(db: Session, doc: Document, progress: dict[str, Any]) -> None:
    db.flush()
    db.refresh(doc)
    merged = _merge_progress(get_progress(doc), progress)
    meta = dict(doc.meta or {})
    meta["question_progress"] = merged
    doc.meta = meta
    flag_modified(doc, "meta")
    user = doc.account_id
    if user:
        captured = doc.artifact_captured_at or doc.created_at
        workspace_repo.upsert_workspace(
            db,
            account_id=user,
            artifact_id=doc.id,
            artifact_captured_at=captured,
            current_page=merged.get("current_page"),
            pool_available_count=_count_available(db, doc.id, merged),
            pool_target=REFILL_BATCH_SIZE,
        )


def _page_key(page: int) -> str:
    return str(page)


def get_page_coverage(doc: Document, page: int) -> dict[str, Any]:
    progress = get_progress(doc)
    coverage = progress.get("page_coverage") or {}
    entry = coverage.get(_page_key(page))
    return entry if isinstance(entry, dict) else {}


def save_page_coverage(
    db: Session,
    document_id: uuid.UUID,
    *,
    page: int,
    question_budget: int,
    aspects: list[dict[str, Any]],
    rationale: str = "",
    triage_activity_id: str | None = None,
) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    entry = {
        "question_budget": question_budget,
        "aspects": aspects,
        "coverage_complete": False,
        "rationale": rationale,
        "triage_activity_id": triage_activity_id,
    }
    save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})
    db.commit()
    db.refresh(doc)


def get_question_budget(doc: Document, page: int) -> int:
    cov = get_page_coverage(doc, page)
    budget = int(cov.get("question_budget") or INITIAL_BATCH_SIZE)
    return max(INITIAL_BATCH_SIZE, min(ABSOLUTE_MAX_QUESTIONS_PER_PAGE, budget))


def is_coverage_complete(doc: Document, page: int) -> bool:
    cov = get_page_coverage(doc, page)
    if cov.get("coverage_complete"):
        return True
    aspects = cov.get("aspects") or []
    if not aspects:
        return False
    return all(a.get("asked") for a in aspects)


def count_assertions_on_page(db: Session, document_id: uuid.UUID, page: int) -> int:
    return int(
        db.execute(
            text(
                """
                SELECT COUNT(*)::int FROM intel.assertion
                WHERE payload->>'artifact_id' = :artifact_id
                  AND status = 'active'
                  AND (payload->>'page_number')::int = :page
                """
            ),
            {"artifact_id": str(document_id), "page": page},
        ).scalar()
        or 0
    )


def count_answered_on_page(
    db: Session,
    document_id: uuid.UUID,
    page: int,
    answered_ids: list[str],
) -> int:
    if not answered_ids:
        return 0
    return int(
        db.execute(
            text(
                """
                SELECT COUNT(*)::int FROM intel.assertion
                WHERE payload->>'artifact_id' = :artifact_id
                  AND status = 'active'
                  AND (payload->>'page_number')::int = :page
                  AND id::text = ANY(:answered)
                """
            ),
            {"artifact_id": str(document_id), "page": page, "answered": answered_ids},
        ).scalar()
        or 0
    )


def _count_available(db: Session, document_id: uuid.UUID, progress: dict[str, Any]) -> int:
    page = int(progress.get("current_page") or 1)
    answered = {str(x) for x in progress.get("answered_ids") or []}
    rows = db.execute(
        text(
            """
            SELECT id::text FROM intel.assertion
            WHERE payload->>'artifact_id' = :artifact_id
              AND status = 'active'
              AND (payload->>'page_number')::int = :page
            ORDER BY (payload->>'sequence')::int ASC
            """
        ),
        {"artifact_id": str(document_id), "page": page},
    ).scalars().all()
    return sum(1 for row_id in rows if row_id not in answered)


def next_assertion_id(db: Session, document_id: uuid.UUID, progress: dict[str, Any]) -> str | None:
    page = int(progress.get("current_page") or 1)
    answered = {str(x) for x in progress.get("answered_ids") or []}
    rows = db.execute(
        text(
            """
            SELECT id::text FROM intel.assertion
            WHERE payload->>'artifact_id' = :artifact_id
              AND status = 'active'
              AND (payload->>'page_number')::int = :page
            ORDER BY (payload->>'sequence')::int ASC
            """
        ),
        {"artifact_id": str(document_id), "page": page},
    ).scalars().all()
    for row_id in rows:
        if row_id not in answered:
            return row_id
    return None


def selected_page_list(doc: Document) -> list[int]:
    """Ordered study pages — explicit list or contiguous from/to range."""
    selected = (doc.meta or {}).get("selected_range") or {}
    pages = selected.get("pages")
    if isinstance(pages, list) and pages:
        return sorted({int(p) for p in pages if int(p) >= 1})
    lo = int(selected.get("from") or 1)
    hi = int(selected.get("to") or lo)
    return list(range(lo, hi + 1))


def page_range_bounds(doc: Document) -> tuple[int, int]:
    pages = selected_page_list(doc)
    if not pages:
        return 1, 1
    return pages[0], pages[-1]


def is_page_complete(db: Session, doc: Document, progress: dict[str, Any]) -> bool:
    if progress.get("generation_pending"):
        return False
    if next_assertion_id(db, doc.id, progress):
        return False
    page = int(progress.get("current_page") or 1)
    budget = get_question_budget(doc, page)
    generated = count_assertions_on_page(db, doc.id, page)
    if generated == 0:
        return False
    if is_coverage_complete(doc, page):
        return True
    return generated >= budget


def build_learn_queue_state(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    progress: dict[str, Any],
) -> dict[str, Any]:
    page = int(progress.get("current_page") or 1)
    study_pages = selected_page_list(doc)
    page_from, page_to = page_range_bounds(doc)
    answered_ids = [str(x) for x in progress.get("answered_ids") or []]
    questions_generated = count_assertions_on_page(db, document_id, page)
    questions_answered = count_answered_on_page(db, document_id, page, answered_ids)
    budget = get_question_budget(doc, page)
    next_id = next_assertion_id(db, document_id, progress)
    coverage_complete = is_coverage_complete(doc, page)
    page_complete = is_page_complete(db, doc, progress)
    last_study_page = study_pages[-1] if study_pages else page_to
    document_complete = page_complete and page == last_study_page

    from app.services.rag_window import get_rag_window, is_rag_window_ready

    rag_pages = get_rag_window(doc)
    rag_ready = is_rag_window_ready(db, document_id, doc)

    return {
        "current_page": page,
        "page_from": page_from,
        "page_to": page_to,
        "current_assertion_id": next_id,
        "question_number": questions_answered + 1 if next_id else questions_answered,
        "question_budget": budget,
        "questions_answered": questions_answered,
        "questions_generated": questions_generated,
        "generation_pending": bool(progress.get("generation_pending")),
        "page_triage_complete": bool(get_page_coverage(doc, page)),
        "coverage_complete": coverage_complete,
        "page_complete": page_complete,
        "document_complete": document_complete,
        "pool_available": _count_available(db, document_id, progress),
        "generated_on_page": questions_generated,
        "answered_on_page": questions_answered,
        "max_per_page": budget,
        "rag_window_pages": rag_pages,
        "rag_window_ready": rag_ready,
    }


def enqueue_page_triage(db: Session, doc: Document, *, page: int) -> Job:
    save_progress(db, doc, {"generation_pending": True})

    activity_id = create_activity(
        db,
        type_uri="/vocab/activity/generate_questions",
        agent="question_pool.page_triage",
        source_slug="user-upload",
        stats={"artifact_id": str(doc.id), "page_number": page},
    )
    job = enqueue_generate(
        db,
        document_id=doc.id,
        account_id=doc.account_id,
        activity_id=activity_id,
        options={
            "mode": "page_triage",
            "artifact_id": str(doc.id),
            "page_number": page,
        },
    )
    db.commit()
    return job


def enqueue_page_batch(
    db: Session,
    doc: Document,
    *,
    page: int,
    batch_size: int,
    start_sequence: int,
) -> Job:
    save_progress(db, doc, {"generation_pending": True})

    activity_id = create_activity(
        db,
        type_uri="/vocab/activity/generate_questions",
        agent="question_pool.page_batch",
        source_slug="user-upload",
        stats={
            "artifact_id": str(doc.id),
            "page_number": page,
            "batch_size": batch_size,
            "start_sequence": start_sequence,
        },
    )
    job = enqueue_generate(
        db,
        document_id=doc.id,
        account_id=doc.account_id,
        activity_id=activity_id,
        options={
            "mode": "page_batch",
            "artifact_id": str(doc.id),
            "page_number": page,
            "batch_size": batch_size,
            "start_sequence": start_sequence,
        },
    )
    db.commit()
    return job


def on_triage_completed(db: Session, document_id: uuid.UUID, *, page: int) -> Job | None:
    doc = db.get(Document, document_id)
    if not doc:
        return None
    progress = get_progress(doc)
    if int(progress.get("current_page") or 0) != page:
        save_progress(db, doc, {"generation_pending": False})
        db.commit()
        return None
    save_progress(db, doc, {"generation_pending": False})
    db.commit()

    budget = get_question_budget(doc, page)
    generated = count_assertions_on_page(db, document_id, page)
    if generated > 0:
        return None
    batch = min(INITIAL_BATCH_SIZE, budget)
    return enqueue_page_batch(db, doc, page=page, batch_size=batch, start_sequence=0)


def enqueue_initial_pool(db: Session, document_id: uuid.UUID) -> Job | None:
    doc = db.get(Document, document_id)
    if not doc or doc.status != "ready":
        return None

    meta = dict(doc.meta or {})
    if meta.get("no_searchable_text"):
        return None
    if meta.get("question_pool_initialized"):
        return None

    page_from, _ = page_range_bounds(doc)
    progress = default_progress(doc)
    progress["current_page"] = page_from
    meta["question_pool_initialized"] = True
    meta["question_progress"] = progress
    doc.meta = meta
    flag_modified(doc, "meta")
    db.commit()

    return enqueue_page_triage(db, doc, page=page_from)


def reset_for_new_page_range(db: Session, doc: Document, selected: dict[str, Any]) -> None:
    """Clear study progress so a new page-range selection starts fresh."""
    pages_raw = selected.get("pages")
    if isinstance(pages_raw, list) and pages_raw:
        study_pages = sorted({int(p) for p in pages_raw if int(p) >= 1})
        page_from = study_pages[0]
        page_to = study_pages[-1]
        range_meta: dict[str, Any] = {"from": page_from, "to": page_to, "pages": study_pages}
    else:
        page_from = int(selected["from"])
        page_to = int(selected["to"])
        range_meta = {"from": page_from, "to": page_to}
    progress = default_progress(doc)
    progress["current_page"] = page_from
    meta = dict(doc.meta or {})
    meta["selected_range"] = range_meta
    meta.pop("question_pool_initialized", None)
    meta.pop("rag_window", None)
    meta.pop("rag_window_ready", None)
    meta["question_progress"] = progress
    doc.meta = meta
    flag_modified(doc, "meta")
    doc.index_progress = 0

    db.execute(
        text(
            """
            UPDATE qb.jobs
            SET status = 'cancelled'
            WHERE payload->>'document_id' = :document_id
              AND status IN ('queued', 'running')
            """
        ),
        {"document_id": str(doc.id)},
    )

    if doc.account_id:
        captured = doc.artifact_captured_at or doc.created_at
        workspace_repo.upsert_workspace(
            db,
            account_id=doc.account_id,
            artifact_id=doc.id,
            artifact_captured_at=captured,
            current_page=page_from,
            status="indexing",
            selected_range=meta["selected_range"],
            pool_available_count=0,
            pool_target=REFILL_BATCH_SIZE,
        )


def on_batch_completed(db: Session, document_id: uuid.UUID, *, page: int, saved: int) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    progress = get_progress(doc)
    patch: dict[str, Any] = {"generation_pending": False}
    if int(progress.get("current_page") or 0) == page:
        patch["generated_on_page"] = count_assertions_on_page(db, document_id, page)
    save_progress(db, doc, patch)
    db.commit()


def on_batch_failed(db: Session, document_id: uuid.UUID, *, page: int) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    progress = get_progress(doc)
    if page and int(progress.get("current_page") or 0) == page:
        save_progress(db, doc, {"generation_pending": False})
        db.commit()


def mark_aspect_asked(db: Session, document_id: uuid.UUID, page: int, aspect_key: str) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    entry = dict(get_page_coverage(doc, page))
    aspects = list(entry.get("aspects") or [])
    for aspect in aspects:
        if aspect.get("key") == aspect_key:
            aspect["asked"] = True
    entry["aspects"] = aspects
    save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})


def set_coverage_complete(db: Session, document_id: uuid.UUID, page: int) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    entry = dict(get_page_coverage(doc, page))
    entry["coverage_complete"] = True
    save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})


def _has_running_generate_job(db: Session, document_id: uuid.UUID) -> bool:
    running = db.execute(
        text(
            """
            SELECT 1 FROM jobs
            WHERE name = 'generate.questions'
              AND payload->>'document_id' = :document_id
              AND status = 'running'
            LIMIT 1
            """
        ),
        {"document_id": str(document_id)},
    ).scalar()
    return running is not None


def _has_active_generate_job(db: Session, document_id: uuid.UUID) -> bool:
    active = db.execute(
        text(
            """
            SELECT 1 FROM jobs
            WHERE name = 'generate.questions'
              AND payload->>'document_id' = :document_id
              AND status IN ('queued', 'running')
            LIMIT 1
            """
        ),
        {"document_id": str(document_id)},
    ).scalar()
    return active is not None


def _cancel_queued_generate_jobs(db: Session, document_id: uuid.UUID) -> None:
    db.execute(
        text(
            """
            UPDATE jobs
            SET status = 'cancelled'
            WHERE name = 'generate.questions'
              AND payload->>'document_id' = :document_id
              AND status = 'queued'
            """
        ),
        {"document_id": str(document_id)},
    )


def release_stuck_generation(db: Session, document_id: uuid.UUID) -> None:
    """Clear a stale generation_pending flag when no job is actually running."""
    if _has_running_generate_job(db, document_id):
        return
    doc = db.get(Document, document_id)
    if not doc:
        return
    db.refresh(doc)
    progress = get_progress(doc)
    if not progress.get("generation_pending"):
        return
    _cancel_queued_generate_jobs(db, document_id)
    save_progress(db, doc, {"generation_pending": False})
    db.commit()


def kick_generation_sync(db: Session, document_id: uuid.UUID) -> None:
    """Run triage + first batch inline when the pool is empty and no worker job is active."""
    if _has_active_generate_job(db, document_id):
        return

    doc = db.get(Document, document_id)
    if not doc or doc.status != "ready":
        return

    db.refresh(doc)

    progress = get_progress(doc)
    if next_assertion_id(db, document_id, progress):
        return

    page = int(progress.get("current_page") or page_range_bounds(doc)[0])

    from app.graphs.generation_graph import run_generation

    if not get_page_coverage(doc, page):
        run_generation(
            db,
            document_id,
            {"mode": "page_triage", "page_number": page},
        )
        db.refresh(doc)

    if count_assertions_on_page(db, document_id, page) == 0 and not _has_active_generate_job(
        db, document_id
    ):
        budget = get_question_budget(doc, page)
        run_generation(
            db,
            document_id,
            {
                "mode": "page_batch",
                "page_number": page,
                "batch_size": min(INITIAL_BATCH_SIZE, budget),
                "start_sequence": 0,
            },
        )


def _enqueue_pool_work(db: Session, document_id: uuid.UUID, doc: Document) -> Job | None:
    if _has_active_generate_job(db, document_id):
        return None
    db.refresh(doc)
    meta = dict(doc.meta or {})
    progress = get_progress(doc)
    if not meta.get("question_pool_initialized"):
        return enqueue_initial_pool(db, document_id)

    page = int(progress.get("current_page") or page_range_bounds(doc)[0])
    if not get_page_coverage(doc, page):
        return enqueue_page_triage(db, doc, page=page)

    generated = count_assertions_on_page(db, document_id, page)
    if generated == 0:
        budget = get_question_budget(doc, page)
        return enqueue_page_batch(
            db,
            doc,
            page=page,
            batch_size=min(INITIAL_BATCH_SIZE, budget),
            start_sequence=0,
        )
    return None


def clear_stale_generation_pending(db: Session, doc: Document) -> None:
    release_stuck_generation(db, doc.id)


def advance_to_next_page(db: Session, doc: Document) -> Job | None:
    progress = get_progress(doc)
    study_pages = selected_page_list(doc)
    page = int(progress.get("current_page") or (study_pages[0] if study_pages else 1))
    try:
        idx = study_pages.index(page)
    except ValueError:
        return None
    if idx >= len(study_pages) - 1:
        return None

    new_page = study_pages[idx + 1]
    save_progress(
        db,
        doc,
        {
            "current_page": new_page,
            "answered_on_page": 0,
            "generated_on_page": 0,
            "generation_pending": False,
        },
    )
    db.commit()

    if not get_page_coverage(doc, new_page):
        return enqueue_page_triage(db, doc, page=new_page)
    generated = count_assertions_on_page(db, doc.id, new_page)
    if generated == 0:
        budget = get_question_budget(doc, new_page)
        return enqueue_page_batch(
            db,
            doc,
            page=new_page,
            batch_size=min(INITIAL_BATCH_SIZE, budget),
            start_sequence=0,
        )
    from app.services.rag_window import is_rag_window_ready

    if not is_rag_window_ready(db, doc.id, doc):
        return enqueue_rag_window(
            db,
            doc.id,
            account_id=doc.account_id,
            current_page=new_page,
        )
    return None


def ensure_question_pool(db: Session, document_id: uuid.UUID) -> Job | None:
    """Kick off or recover question generation for ready documents."""
    doc = db.get(Document, document_id)
    if not doc or doc.status != "ready":
        return None

    db.refresh(doc)
    meta = dict(doc.meta or {})
    if meta.get("no_searchable_text"):
        return None

    release_stuck_generation(db, document_id)
    db.refresh(doc)

    progress = get_progress(doc)
    if next_assertion_id(db, document_id, progress):
        return None

    job: Job | None = None
    if not _has_active_generate_job(db, document_id):
        job = _enqueue_pool_work(db, document_id, doc)
        kick_generation_sync(db, document_id)

    return job


def is_transition_prep_done(doc: Document, page: int) -> bool:
    progress = get_progress(doc)
    done = progress.get("transition_prep_done") or {}
    return bool(done.get(_page_key(page)))


def mark_transition_prep_done(db: Session, doc: Document, page: int) -> None:
    done = dict(get_progress(doc).get("transition_prep_done") or {})
    done[_page_key(page)] = True
    save_progress(db, doc, {"transition_prep_done": done})


def should_transition_prefetch(answered_on_page: int, budget: int) -> bool:
    if budget <= 0:
        return False
    return answered_on_page / budget > TRANSITION_PREFETCH_RATIO


def maybe_transition_prefetch(db: Session, document_id: uuid.UUID) -> Job | None:
    doc = db.get(Document, document_id)
    if not doc or doc.status != "ready":
        return None
    progress = get_progress(doc)
    page = int(progress.get("current_page") or 1)
    if is_transition_prep_done(doc, page):
        return None
    budget = get_question_budget(doc, page)
    answered_on_page = int(progress.get("answered_on_page") or 0)
    if not should_transition_prefetch(answered_on_page, budget):
        return None
    return enqueue_transition_prep(
        db,
        document_id,
        current_page=page,
        account_id=doc.account_id,
    )


def record_answer(db: Session, document_id: uuid.UUID, assertion_id: uuid.UUID) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    progress = get_progress(doc)
    aid = str(assertion_id)
    answered = [str(x) for x in progress.get("answered_ids") or []]
    if aid in answered:
        return
    answered.append(aid)
    patch: dict[str, Any] = {
        "answered_ids": answered,
        "answered_on_page": int(progress.get("answered_on_page") or 0) + 1,
    }
    row = db.execute(
        text(
            """
            SELECT payload->>'primary_concept_key' AS key,
                   (payload->>'page_number')::int AS page
            FROM intel.assertion WHERE id = :id
            """
        ),
        {"id": assertion_id},
    ).mappings().first()
    if row and row.get("key") and row.get("page"):
        page_num = int(row["page"])
        entry = dict(get_page_coverage(doc, page_num))
        aspects = list(entry.get("aspects") or [])
        for aspect in aspects:
            if aspect.get("key") == row["key"]:
                aspect["answered"] = True
        entry["aspects"] = aspects
        patch["page_coverage"] = {_page_key(page_num): entry}
    save_progress(db, doc, patch)
    db.commit()
    maybe_refill_pool(db, document_id)
    maybe_transition_prefetch(db, document_id)


def maybe_refill_pool(db: Session, document_id: uuid.UUID) -> Job | None:
    doc = db.get(Document, document_id)
    if not doc or doc.status != "ready":
        return None

    progress = get_progress(doc)
    if progress.get("generation_pending"):
        return None

    page = int(progress.get("current_page") or 1)
    budget = get_question_budget(doc, page)
    generated_on_page = count_assertions_on_page(db, document_id, page)
    answered_ids = [str(x) for x in progress.get("answered_ids") or []]
    answered_on_page = count_answered_on_page(db, document_id, page, answered_ids)
    available = _count_available(db, doc.id, progress)

    if is_coverage_complete(doc, page) or generated_on_page >= budget:
        return None

    if (
        answered_on_page > 0
        and answered_on_page % REFILL_AFTER_ANSWERED == 0
        and generated_on_page < budget
    ):
        remaining = budget - generated_on_page
        batch = min(REFILL_BATCH_SIZE, remaining)
        if batch > 0:
            return enqueue_page_batch(
                db,
                doc,
                page=page,
                batch_size=batch,
                start_sequence=generated_on_page,
            )

    if available == 0 and generated_on_page < budget:
        remaining = budget - generated_on_page
        batch = min(REFILL_BATCH_SIZE, remaining)
        if batch <= 0:
            return None
        return enqueue_page_batch(
            db,
            doc,
            page=page,
            batch_size=batch,
            start_sequence=generated_on_page,
        )

    return None
