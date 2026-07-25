"""Question-pool job orchestration — triage/generation/transition enqueue, batch
and triage callbacks, stale-job reclaim, page transitions, and answer recording.

Split out of question_pool.py to keep that module focused on the core state +
selection layer. This is the TOP layer: it depends on the core (imported below)
but nothing in core depends on it. External code should keep importing from the
`app.services.question_pool` facade, which re-exports everything here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import Document, Job
from app.repositories.intel import create_activity
from app.repositories import workspace as workspace_repo
from app.services.document_learn_state import save_progress_row
from app.services.jobs import enqueue_generate, enqueue_rag_window, enqueue_transition_prep
from app.services.question_pool import (
    EAGER_TRIAGE_LOOKAHEAD,
    FIRST_QUESTION_BATCH_SIZE,
    GENERATE_JOB_STALE_SECONDS,
    INITIAL_BATCH_SIZE,
    MAX_GENERATE_BATCH_SIZE,
    REFILL_AFTER_ANSWERED,
    REFILL_BATCH_SIZE,
    _count_available,
    _page_key,
    count_answered_on_page,
    count_assertions_on_page,
    default_progress,
    effective_question_budget,
    get_page_coverage,
    get_progress,
    get_question_budget,
    is_coverage_complete,
    next_assertion_id,
    page_range_bounds,
    save_progress,
    selected_page_list,
)

logger = logging.getLogger(__name__)

def enqueue_page_triage(db: Session, doc: Document, *, page: int, precompute: bool = False) -> Job:
    # Eager precompute triage for non-current pages must not touch the doc-level
    # generation_pending flag (which tracks only the current page's generation).
    if not precompute:
        save_progress(db, doc, {"generation_pending": True})

    activity_id = create_activity(
        db,
        type_uri="/vocab/activity/generate_questions",
        agent="question_pool.page_triage",
        source_slug="user-upload",
        stats={"artifact_id": str(doc.id), "page_number": page, "precompute": precompute},
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
            "precompute": precompute,
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
) -> Job | None:
    generated = count_assertions_on_page(db, doc.id, page)
    start_sequence = max(start_sequence, generated)
    progress = get_progress(doc)
    budget = effective_question_budget(doc, page, progress)
    remaining = budget - generated
    current_page = int(progress.get("current_page") or page_range_bounds(doc)[0])
    is_current_page = page == current_page
    if remaining <= 0:
        if is_current_page:
            save_progress(db, doc, {"generation_pending": False})
            db.commit()
        return None

    # Never enqueue the whole remaining page plan in one job — small rolling
    # batches so Q1 lands fast and the learner studies while the pool refills.
    batch_size = min(batch_size, remaining, MAX_GENERATE_BATCH_SIZE)
    # Per-page guard: a different page (e.g. the next page's transition prefetch)
    # may be generating concurrently — only block on a job for THIS page.
    if _has_active_generate_job_for_page(db, doc.id, page):
        # Only the current page's in-flight batch owns generation_pending. A
        # next-page prefetch must not freeze current-page refill / page-complete.
        if is_current_page:
            save_progress(db, doc, {"generation_pending": True})
            db.commit()
        return None

    _cancel_queued_generate_jobs_for_page(db, doc.id, page)
    if is_current_page:
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


def _initial_pool_target(doc: Document, page: int) -> int:
    return min(INITIAL_BATCH_SIZE, get_question_budget(doc, page))


def _maybe_enqueue_initial_pool_remainder(
    db: Session, doc: Document, *, page: int
) -> Job | None:
    """After the first question lands, fill the warm pool up to INITIAL_BATCH_SIZE."""
    generated = count_assertions_on_page(db, doc.id, page)
    target = _initial_pool_target(doc, page)
    if generated >= target:
        return None
    if _has_active_generate_job_for_page(db, doc.id, page):
        return None
    remaining = min(REFILL_BATCH_SIZE, target - generated)
    if remaining <= 0:
        return None
    return enqueue_page_batch(
        db,
        doc,
        page=page,
        batch_size=remaining,
        start_sequence=generated,
    )


def _enqueue_first_question_batch(
    db: Session, doc: Document, *, page: int
) -> Job | None:
    generated = count_assertions_on_page(db, doc.id, page)
    if generated > 0:
        return _maybe_enqueue_initial_pool_remainder(db, doc, page=page)
    budget = get_question_budget(doc, page)
    batch = min(FIRST_QUESTION_BATCH_SIZE, budget)
    if batch <= 0:
        return None
    return enqueue_page_batch(db, doc, page=page, batch_size=batch, start_sequence=0)


def maybe_enqueue_early_page_triage(
    db: Session, document_id: uuid.UUID, *, page_number: int
) -> Job | None:
    """Overlap triage with RAG page ingest so Learn does not wait on triage after ready."""
    doc = db.get(Document, document_id)
    if not doc:
        return None
    meta = dict(doc.meta or {})
    if meta.get("no_searchable_text") or not meta.get("selected_range"):
        return None
    page_from, _ = page_range_bounds(doc)
    if page_number != page_from:
        return None
    if get_page_coverage(doc, page_number):
        return None
    if _has_active_generate_job_for_page(db, document_id, page_number):
        return None
    progress = get_progress(doc)
    progress.setdefault("current_page", page_from)
    if not meta.get("question_pool_initialized"):
        meta["question_progress"] = progress
        doc.meta = meta
        flag_modified(doc, "meta")
        db.commit()
    return enqueue_page_triage(db, doc, page=page_number)


def on_triage_completed(
    db: Session, document_id: uuid.UUID, *, page: int, precompute: bool = False
) -> Job | None:
    doc = db.get(Document, document_id)
    if not doc:
        return None
    # Precompute triage only populates page coverage ahead of time — it must not
    # touch the current-page generation_pending flag or auto-generate a batch.
    if precompute:
        return None
    progress = get_progress(doc)
    if int(progress.get("current_page") or 0) != page:
        save_progress(db, doc, {"generation_pending": False})
        db.commit()
        return None
    save_progress(db, doc, {"generation_pending": False})
    db.commit()

    budget = get_question_budget(doc, page)
    # Non-content / zero-yield page: nothing to generate. The learn queue will
    # treat it as complete and advance past it.
    if budget <= 0:
        # Even a non-content page can be programmable (e.g. a code-only snippet
        # the LLM won't turn into MCQs but is perfect for a coding challenge).
        _maybe_spawn_coding(db, doc, page=page)
        return None
    generated = count_assertions_on_page(db, document_id, page)
    if generated > 0:
        _maybe_spawn_coding(db, doc, page=page)
        return None
    batch = min(FIRST_QUESTION_BATCH_SIZE, budget)
    job = enqueue_page_batch(db, doc, page=page, batch_size=batch, start_sequence=0)
    # Coding generation is independent of MCQ budget — a programmable page spawns
    # one coding problem whether or not MCQs are also being generated for it.
    _maybe_spawn_coding(db, doc, page=page)
    return job


def _maybe_spawn_coding(db: Session, doc: Document, *, page: int) -> None:
    """Spawn coding generation when triage flagged the page as programmable.

    Best-effort: a failure here (e.g. job table temporarily unavailable) must not
    roll back the MCQ batch enqueue that just happened. The feature degrades to
    'no coding problems for this page' silently.
    """
    coverage = get_page_coverage(doc, page)
    if not coverage.get("programmable"):
        return
    try:
        from app.services.question_generation import enqueue_coding_generation_for_page

        enqueue_coding_generation_for_page(
            db, doc.id, page=page, account_id=doc.account_id
        )
    except Exception:
        logger.warning(
            "coding generation enqueue failed for doc=%s page=%s",
            doc.id, page, exc_info=True,
        )


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

    if get_page_coverage(doc, page_from):
        job = _enqueue_first_question_batch(db, doc, page=page_from)
    else:
        # Triage off the critical path: enqueue triage AND the first batch in
        # parallel. The batch uses speculative (heuristic) aspects when triage
        # hasn't landed yet — _run_page_batch synthesizes aspect targets from
        # the page text if coverage is absent. Real triage lands ~5s later and
        # refines the plan for subsequent batches. This removes the triage LLM
        # call from the path the user waits on.
        enqueue_page_triage(db, doc, page=page_from)
        job = _enqueue_first_question_batch(db, doc, page=page_from)
    # Eagerly triage the next few pages in the background so the document is
    # understood ahead of the reader and transitions never wait on triage.
    _enqueue_eager_triage_lookahead(db, doc, from_page=page_from)
    return job


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
    save_progress_row(db, doc.id, progress)
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

    study_pages_list = range_meta.get("pages") or list(range(page_from, page_to + 1))
    db.execute(
        text(
            """
            UPDATE intel.assertion
            SET status = 'retracted'
            WHERE payload->>'artifact_id' = :artifact_id
              AND status = 'active'
              AND NOT ((payload->>'page_number')::int = ANY(:pages))
            """
        ),
        {"artifact_id": str(doc.id), "pages": [int(p) for p in study_pages_list]},
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
    current_page = int(progress.get("current_page") or 0)
    if current_page == page:
        patch["generated_on_page"] = count_assertions_on_page(db, document_id, page)
    save_progress(db, doc, patch)
    db.commit()
    # Precompute per-option grade feedback for this page's questions off the answer
    # path (option #4), so grading is an instant lookup. Best-effort — never let a
    # coaching-enqueue hiccup affect generation flow.
    if saved:
        try:
            from app.services.jobs import enqueue_coach_page

            enqueue_coach_page(db, document_id=document_id, page=page, account_id=doc.account_id)
        except Exception:
            # Grading still works (verdict is instant; feedback falls back to the live
            # path + lazy warm-back), so this never blocks generation — but warn, don't
            # swallow: a silent debug-level failure here is what hid a stale-worker miss.
            logger.warning("coach enqueue failed for doc=%s page=%s", document_id, page, exc_info=True)
    if current_page == page:
        _maybe_enqueue_initial_pool_remainder(db, doc, page=page)
        # Chain the next refill immediately when still below low-water. Waiting
        # for the next answer used to leave a drained pool with no job queued
        # after a batch finished — the classic empty-spinner cliff.
        maybe_refill_pool(db, document_id)


def on_batch_failed(db: Session, document_id: uuid.UUID, *, page: int) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    progress = get_progress(doc)
    if page and int(progress.get("current_page") or 0) == page:
        save_progress(db, doc, {"generation_pending": False})
        db.commit()


def mark_aspect_asked(db: Session, document_id: uuid.UUID, page: int, aspect_key: str) -> None:
    mark_aspects_asked(db, document_id, page, [aspect_key])


def mark_aspects_asked(
    db: Session, document_id: uuid.UUID, page: int, aspect_keys: list[str]
) -> None:
    """Mark multiple aspects asked in one coverage read + one progress write.

    Replaces N calls to mark_aspect_asked (each did its own get_page_coverage +
    save_progress = O(N) DB round trips) with a single batched update.
    """
    if not aspect_keys:
        return
    doc = db.get(Document, document_id)
    if not doc:
        return
    entry = dict(get_page_coverage(doc, page))
    aspects = list(entry.get("aspects") or [])
    wanted = set(aspect_keys)
    for aspect in aspects:
        if aspect.get("key") in wanted:
            aspect["asked"] = True
    entry["aspects"] = aspects
    save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})


def bump_aspect_attempts(
    db: Session, document_id: uuid.UUID, page: int, aspect_keys: set[str] | list[str]
) -> None:
    """Record a failed generation attempt for each still-unasked aspect, and
    abandon (mark asked) one that has now failed MAX_ASPECT_ATTEMPTS times.

    Without this, an aspect generation can never satisfy (every draft rejected by
    the verifier/critic/dedup) stays unasked forever, so coverage never completes
    and the learner is stranded once every producible question is answered.
    """
    from app.services.aspect_discovery import should_abandon_aspect

    wanted = set(aspect_keys)
    if not wanted:
        return
    doc = db.get(Document, document_id)
    if not doc:
        return
    entry = dict(get_page_coverage(doc, page))
    aspects = list(entry.get("aspects") or [])
    changed = False
    for aspect in aspects:
        if aspect.get("key") in wanted and not aspect.get("asked"):
            attempts = int(aspect.get("gen_attempts") or 0) + 1
            aspect["gen_attempts"] = attempts
            if should_abandon_aspect(attempts).abandon:
                aspect["asked"] = True
                aspect["abandoned"] = True
            changed = True
    if changed:
        entry["aspects"] = aspects
        save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})
        db.commit()


def set_coverage_complete(db: Session, document_id: uuid.UUID, page: int) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    entry = dict(get_page_coverage(doc, page))
    if not entry.get("aspects"):
        return
    entry["coverage_complete"] = True
    save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})


def clear_stale_coverage_complete(db: Session, document_id: uuid.UUID, page: int) -> bool:
    """Drop coverage_complete when triage never landed (pre-index race poison)."""
    doc = db.get(Document, document_id)
    if not doc:
        return False
    entry = dict(get_page_coverage(doc, page))
    if not entry.get("coverage_complete") or entry.get("aspects"):
        return False
    if count_assertions_on_page(db, document_id, page) > 0:
        return False
    entry.pop("coverage_complete", None)
    if entry:
        save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})
    else:
        progress = get_progress(doc)
        coverage = dict(progress.get("page_coverage") or {})
        coverage.pop(_page_key(page), None)
        save_progress(db, doc, {"page_coverage": coverage})
    db.commit()
    return True


def _reclaim_stale_generate_jobs(db: Session, document_id: uuid.UUID | None = None) -> int:
    """Re-queue orphaned generate.questions jobs so learn mode can recover.

    Preserves ``result`` (incl. generation checkpoint) so a resumed run can
    continue from the last saved sequence instead of redoing LLM work.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=GENERATE_JOB_STALE_SECONDS)
    doc_filter = ""
    params: dict[str, Any] = {"cutoff": cutoff}
    if document_id is not None:
        doc_filter = "AND payload->>'document_id' = :document_id"
        params["document_id"] = str(document_id)
    result = db.execute(
        text(
            f"""
            UPDATE jobs
            SET status = 'queued',
                locked_by = NULL,
                locked_at = NULL,
                error = NULL,
                updated_at = NOW()
            WHERE name = 'generate.questions'
              AND status = 'running'
              AND locked_at IS NOT NULL
              AND locked_at < :cutoff
              {doc_filter}
            """
        ),
        params,
    )
    db.commit()
    return int(result.rowcount or 0)


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


def _has_active_generate_job_for_page(db: Session, document_id: uuid.UUID, page: int) -> bool:
    """Like _has_active_generate_job but scoped to one page AND to batch mode.

    Lets different pages of the same document generate concurrently (e.g. the
    current page's pool plus the next page's transition prefetch) while still
    preventing a duplicate BATCH job for the same page. A page_triage job for
    the same page does NOT block a batch — triage and the first batch run in
    parallel (triage off the critical path), with the batch using speculative
    aspects until triage lands.
    """
    active = db.execute(
        text(
            """
            SELECT 1 FROM jobs
            WHERE name = 'generate.questions'
              AND payload->>'document_id' = :document_id
              AND payload->>'page_number' = :page
              AND (payload->>'mode' IS NULL OR payload->>'mode' = 'page_batch')
              AND status IN ('queued', 'running')
            LIMIT 1
            """
        ),
        {"document_id": str(document_id), "page": str(page)},
    ).scalar()
    return active is not None


def _has_active_triage_job_for_page(db: Session, document_id: uuid.UUID, page: int) -> bool:
    """True when a page_triage job for this page is already queued or running.

    Lets the rolling eager-triage re-seed stay idempotent: a page that is already
    being triaged must not get a duplicate triage job (double LLM cost + a race on
    save_page_coverage).
    """
    active = db.execute(
        text(
            """
            SELECT 1 FROM jobs
            WHERE name = 'generate.questions'
              AND payload->>'document_id' = :document_id
              AND payload->>'page_number' = :page
              AND payload->>'mode' = 'page_triage'
              AND status IN ('queued', 'running')
            LIMIT 1
            """
        ),
        {"document_id": str(document_id), "page": str(page)},
    ).scalar()
    return active is not None


def _enqueue_eager_triage_lookahead(db: Session, doc: Document, *, from_page: int) -> None:
    """Keep page triage rolling EAGER_TRIAGE_LOOKAHEAD pages ahead of the reader.

    Triage is cheap (~1 LLM call/page) but must land BEFORE the transition
    prefetch (which fires around TRANSITION_PREFETCH_RATIO of a page) can
    generate the next page's pool — transition_prep only generates a next page
    whose coverage already exists.
    Seeding once at init left the window static: on documents longer than the
    lookahead, every transition past the seeded window hit a cold triage→generate
    when the reader arrived (~30s wait). Re-seeding on each advance keeps the
    window ahead of the reader. Idempotent: skips pages already triaged or with a
    triage job in flight, so calling it on every advance adds no duplicate work.
    """
    study = selected_page_list(doc)
    if from_page not in study:
        return
    start = study.index(from_page) + 1
    for ahead_page in study[start : start + EAGER_TRIAGE_LOOKAHEAD]:
        if get_page_coverage(doc, ahead_page):
            continue
        if _has_active_triage_job_for_page(db, doc.id, ahead_page):
            continue
        enqueue_page_triage(db, doc, page=ahead_page, precompute=True)


def _cancel_queued_generate_jobs_for_page(db: Session, document_id: uuid.UUID, page: int) -> None:
    db.execute(
        text(
            """
            UPDATE jobs
            SET status = 'cancelled'
            WHERE name = 'generate.questions'
              AND payload->>'document_id' = :document_id
              AND payload->>'page_number' = :page
              AND status = 'queued'
            """
        ),
        {"document_id": str(document_id), "page": str(page)},
    )


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
    """Clear a stale generation_pending flag when no job is queued or running."""
    if _has_active_generate_job(db, document_id):
        return
    doc = db.get(Document, document_id)
    if not doc:
        return
    db.refresh(doc)
    progress = get_progress(doc)
    if not progress.get("generation_pending"):
        return
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
                "batch_size": min(FIRST_QUESTION_BATCH_SIZE, budget),
                "start_sequence": 0,
            },
        )


def _enqueue_pool_work(db: Session, document_id: uuid.UUID, doc: Document) -> Job | None:
    db.refresh(doc)
    meta = dict(doc.meta or {})
    progress = get_progress(doc)
    if not meta.get("question_pool_initialized"):
        # Init may enqueue triage + first batch + eager lookahead; only block when
        # *this* doc already has any generate work (avoid double-init races).
        if _has_active_generate_job(db, document_id):
            return None
        return enqueue_initial_pool(db, document_id)

    page = int(progress.get("current_page") or page_range_bounds(doc)[0])
    # Page-scoped: next-page triage/prefetch must not block current-page start.
    if _has_active_generate_job_for_page(db, document_id, page):
        return None
    if not get_page_coverage(doc, page):
        if _has_active_triage_job_for_page(db, document_id, page):
            return None
        return enqueue_page_triage(db, doc, page=page)

    generated = count_assertions_on_page(db, document_id, page)
    if generated == 0:
        return _enqueue_first_question_batch(db, doc, page=page)
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

    # Re-seed the rolling triage window so pages beyond the initial lookahead are
    # understood before the reader reaches them (the transition prefetch can only
    # pre-generate a next page whose coverage already exists).
    _enqueue_eager_triage_lookahead(db, doc, from_page=new_page)

    if not get_page_coverage(doc, new_page):
        return enqueue_page_triage(db, doc, page=new_page)
    generated = count_assertions_on_page(db, doc.id, new_page)
    if generated == 0:
        job = _enqueue_first_question_batch(db, doc, page=new_page)
        if job is not None:
            return job
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
    page = int(progress.get("current_page") or page_range_bounds(doc)[0])
    clear_stale_coverage_complete(db, document_id, page)
    db.refresh(doc)

    from app.services.rag_window import is_rag_window_ready

    rag_job: Job | None = None
    if not is_rag_window_ready(db, document_id, doc):
        rag_job = enqueue_rag_window(
            db,
            document_id,
            account_id=doc.account_id,
            current_page=page,
        )
        db.commit()

    # Always try to keep the warm buffer topped up — even when the learner already
    # has a next card. Returning early here used to skip refill until the next
    # answer, so a 1-deep pool drained to empty on Continue.
    refill_job = maybe_refill_pool(db, document_id)

    if next_assertion_id(db, document_id, progress):
        return refill_job or rag_job

    # Request path is read-only: only ENQUEUE background work, never generate
    # inline. Generation runs in the parallel CPU workers (and is pre-warmed by
    # eager precompute at index-ready), so the learn-queue request returns fast
    # and the client reads from a warm pool — the ≤5s seamless guarantee.
    # Page-scoped: other pages' triage/prefetch must not block current-page cook.
    job: Job | None = refill_job or rag_job
    if refill_job is None:
        pool_job = _enqueue_pool_work(db, document_id, doc)
        if pool_job is not None:
            job = pool_job

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
    from app.services.session_design import evaluate_serve_schedule

    if budget <= 0:
        return False
    return evaluate_serve_schedule(
        answered_on_page=answered_on_page,
        page_budget=budget,
    ).prefetch_transition


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


def save_confirmed_answer(
    db: Session,
    document_id: uuid.UUID,
    assertion_id: uuid.UUID,
    *,
    choice_index: int,
    correct: bool,
    learner_ability: float | None = None,
    item_difficulty: float | None = None,
    ability_se: float | None = None,
    mastery_stop: bool | None = None,
    revisit_hours: float | None = None,
    revisit_ease: float | None = None,
    revisit_repetitions: int | None = None,
) -> None:
    """Persist the learner's latest confirmed MCQ choice for tutor chat context.

    Also records the question's concept key so the selection loop can react to the
    last answer, and mirrors the learner's freshly calibrated ability into progress so
    adaptive selection can target their edge without an extra read. When the
    item's calibrated difficulty is supplied, advances the learner's *per-concept*
    ability via the Calibration Engine. ``learner_ability``/``item_difficulty`` are only
    passed for a genuinely new (non-replayed) answer, so this never double-counts.
    """
    doc = db.get(Document, document_id)
    if not doc:
        return
    concept_key = db.execute(
        text("SELECT payload->>'primary_concept_key' FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).scalar()
    progress = get_progress(doc)
    patch: dict[str, Any] = {
        "last_confirmed_answer": {
            "assertion_id": str(assertion_id),
            "choice_index": int(choice_index),
            "correct": bool(correct),
            "concept_key": concept_key,
        },
        # Session Design: count items answered this soft session.
        "session_items_answered": int(progress.get("session_items_answered") or 0) + 1,
    }
    if learner_ability is not None:
        patch["learner_ability"] = float(learner_ability)
    if ability_se is not None:
        patch["learner_ability_se"] = float(ability_se)
    if mastery_stop is not None:
        patch["mastery_stop"] = bool(mastery_stop)
    if revisit_hours is not None:
        patch["revisit_due_hours"] = float(revisit_hours)
        # Per-concept due map for Spaced Revisit Engine consumers.
        if concept_key:
            due = dict(progress.get("concept_revisit_hours") or {})
            due[str(concept_key)] = float(revisit_hours)
            patch["concept_revisit_hours"] = due
    if revisit_ease is not None:
        patch["revisit_ease"] = float(revisit_ease)
        if concept_key:
            ease_map = dict(progress.get("concept_revisit_ease") or {})
            ease_map[str(concept_key)] = float(revisit_ease)
            patch["concept_revisit_ease"] = ease_map
    if revisit_repetitions is not None:
        patch["revisit_repetitions"] = int(revisit_repetitions)
        if concept_key:
            reps_map = dict(progress.get("concept_revisit_repetitions") or {})
            reps_map[str(concept_key)] = int(revisit_repetitions)
            patch["concept_revisit_repetitions"] = reps_map
    if concept_key and item_difficulty is not None:
        from app.services.calibration_engine import DEFAULT_RATING, update_from_outcome

        progress = get_progress(doc)
        concept_ability = dict(progress.get("concept_ability") or {})
        concept_n = dict(progress.get("concept_ability_n") or {})
        prior = float(concept_ability.get(concept_key, DEFAULT_RATING))
        prior_n = int(concept_n.get(concept_key, 0) or 0)
        verdict = update_from_outcome(
            prior,
            float(item_difficulty),
            bool(correct),
            ability_n=prior_n,
            difficulty_n=0,
        )
        concept_ability[concept_key] = verdict.ability
        concept_n[concept_key] = verdict.ability_n
        patch["concept_ability"] = concept_ability
        patch["concept_ability_n"] = concept_n
    save_progress(db, doc, patch)
    db.commit()


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
    page = int(progress.get("current_page") or 1)
    # Never bail solely on doc-level generation_pending — that flag is also set by
    # current-page work and used to freeze refill while a batch ran, so a fast
    # learner drained the pool with no follow-up job queued. Per-page active-job
    # check respects the same-page lock (no duplicate concurrent batches) while
    # still allowing refill when only another page's prefetch set the flag.
    if _has_active_generate_job_for_page(db, document_id, page):
        return None
    # Triage must populate page_coverage before batch generation can run.
    if not get_page_coverage(doc, page):
        return None
    budget = effective_question_budget(doc, page, progress)
    generated_on_page = count_assertions_on_page(db, document_id, page)
    answered_ids = [str(x) for x in progress.get("answered_ids") or []]
    answered_on_page = count_answered_on_page(db, document_id, page, answered_ids)
    available = _count_available(db, doc.id, progress)

    if is_coverage_complete(doc, page) or generated_on_page >= budget:
        return None

    # Stage another batch whenever the ready buffer is running low, on the periodic
    # answered cadence, or if the pool has fully drained — so there is always a deep
    # backlog of questions ready and the reader never waits on generation.
    from app.services.session_design import evaluate_serve_schedule

    low_water = evaluate_serve_schedule(ready_count=available).refill_now
    periodic = answered_on_page > 0 and answered_on_page % REFILL_AFTER_ANSWERED == 0
    drained = available == 0
    if low_water or periodic or drained:
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

    return None
