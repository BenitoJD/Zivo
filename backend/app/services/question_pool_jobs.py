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

from app.engine_runtime import apply, pick
from app.models import Document, Job, JobWorkload
from app.repositories.intel import create_activity
from app.repositories import workspace as workspace_repo
from app.services.document_learn_state import save_progress_row
from app.services.jobs import enqueue_generate, enqueue_rag_window, enqueue_transition_prep
from app.services.presence import evaluate_presence
from app.services.question_budget import Mode

# Background-prep cap lives in Session Design (orchestration re-exports).
from app.services.session_design import (
    BACKGROUND_PREP_MAX_QUESTIONS as BACKGROUND_PREP_MAX_QUESTIONS,
    evaluate_newspaper_edition_dispatch,
    evaluate_page_batch_enqueue,
    evaluate_pool_wake,
    evaluate_refill_dispatch,
    plan_refill_batch,
)
from app.services.question_pool import (
    FIRST_QUESTION_BATCH_SIZE,
    GENERATE_JOB_STALE_SECONDS,
    INITIAL_BATCH_SIZE,
    MAX_GENERATE_BATCH_SIZE,
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


def _missing(doc: object | None) -> bool:
    return evaluate_presence(doc).action == "missing"


def _is_newspaper_doc(doc: Document) -> bool:
    from app.services.newspaper import is_newspaper_document

    return is_newspaper_document(doc)


def _next_newspaper_cook_page(
    db: Session, document_id: uuid.UUID, doc: Document
) -> tuple[int, Mode] | None:
    """First study page that still needs MCQ batches under learn or test cook budget."""
    from app.services.question_pool import get_test_question_budget
    from app.services.session_design import (
        NewspaperPageCookSignal,
        evaluate_newspaper_page_cook,
    )

    def consider(phase: str, page: int) -> tuple[int, Mode] | None:
        active = _has_active_generate_job_for_page(db, document_id, page)
        cov = get_page_coverage(doc, page)
        non_content = bool((cov or {}).get("non_content"))

        def fill_stats() -> tuple[int, int, bool, int, int]:
            learn_budget = get_question_budget(doc, page, mode="learn")
            learn_generated = count_assertions_on_page(
                db, document_id, page, serve_mode="learn"
            )
            coverage_complete = is_coverage_complete(doc, page)
            test_pair = pick(
                phase == "test",
                lambda: (
                    get_test_question_budget(doc, page),
                    count_assertions_on_page(db, document_id, page, serve_mode="test"),
                ),
                lambda: (0, 0),
            )
            return learn_budget, learn_generated, coverage_complete, test_pair[0], test_pair[1]

        learn_budget, learn_generated, coverage_complete, test_budget, test_generated = pick(
            bool(cov) and not active and not non_content,
            fill_stats,
            lambda: (0, 0, False, 0, 0),
        )
        signal = NewspaperPageCookSignal(
            page=page,
            has_active_generate=active,
            has_coverage=bool(cov),
            non_content=non_content,
            learn_budget=learn_budget,
            learn_generated=learn_generated,
            coverage_complete=coverage_complete,
            test_budget=test_budget,
            test_generated=test_generated,
        )
        verdict = evaluate_newspaper_page_cook(signal, phase=phase)
        return pick(
            verdict.action == "cook" and bool(verdict.cook_mode),
            lambda: (page, verdict.cook_mode),
            lambda: None,
        )

    pairs = (
        (phase, page)
        for phase in ("learn", "test")
        for page in selected_page_list(doc)
    )
    return next(filter(None, (consider(phase, page) for phase, page in pairs)), None)


def _next_newspaper_page_needing_triage(
    db: Session, document_id: uuid.UUID, doc: Document
) -> int | None:
    from app.services.rag_window import pages_ready_for_document

    ready = pages_ready_for_document(db, document_id, doc)

    def consider(page: int) -> int | None:
        return pick(
            page not in ready,
            lambda: None,
            lambda: pick(
                bool(get_page_coverage(doc, page)),
                lambda: None,
                lambda: pick(
                    _has_active_triage_job_for_page(db, document_id, page),
                    lambda: None,
                    lambda: page,
                ),
            ),
        )

    return next(filter(None, map(consider, selected_page_list(doc))), None)


def _maybe_enqueue_newspaper_missing_pages(
    db: Session, document_id: uuid.UUID, doc: Document
) -> Job | None:
    """Queue ingest.page for study pages not yet embedded (full-edition cook)."""
    from app.services.rag_window import pages_ready_for_document
    from app.services.session_design import plan_newspaper_ingest_batch

    study = selected_page_list(doc)
    ready = pages_ready_for_document(db, document_id, doc)
    missing = list(filter(lambda p: p not in ready, study))
    batch = plan_newspaper_ingest_batch(missing)
    return pick(not batch, lambda: None, lambda: _enqueue_ingest_pages(db, document_id, batch))


def _enqueue_ingest_pages(db: Session, document_id: uuid.UUID, batch: list[int]) -> Job | None:
    from app.services.jobs import batch_enqueue_jobs

    jobs = batch_enqueue_jobs(
        db,
        [
            {
                "name": "ingest.page",
                "workload": JobWorkload.cpu,
                "payload": {
                    "document_id": str(document_id),
                    "page_number": page,
                },
            }
            for page in batch
        ],
    )
    db.commit()
    return pick(bool(jobs), lambda: jobs[0], lambda: None)


def _enqueue_newspaper_edition_triage(db: Session, doc: Document) -> Job | None:
    """Precompute triage for the next ingested study page lacking coverage."""
    page = _next_newspaper_page_needing_triage(db, doc.id, doc)
    return pick(
        page is None,
        lambda: None,
        lambda: enqueue_page_triage(db, doc, page=page, precompute=True),
    )


def _maybe_refill_newspaper_edition(
    db: Session, document_id: uuid.UUID, doc: Document
) -> Job | None:
    from app.services.session_design import evaluate_newspaper_edition_tick

    ingest_job = _maybe_enqueue_newspaper_missing_pages(db, document_id, doc)
    cook = _next_newspaper_cook_page(db, document_id, doc)
    cook_page, cook_mode = pick(
        cook is not None, lambda: cook, lambda: (None, None)
    )
    remaining = pick(
        cook_page is not None,
        lambda: get_question_budget(doc, cook_page, mode=cook_mode)
        - count_assertions_on_page(db, document_id, cook_page, serve_mode=cook_mode),
        lambda: 0,
    )
    triage_page = pick(
        cook is None,
        lambda: _next_newspaper_page_needing_triage(db, document_id, doc),
        lambda: None,
    )
    verdict = evaluate_newspaper_edition_tick(
        has_ingest_missing=ingest_job is not None,
        cook_page=cook_page,
        cook_mode=cook_mode,
        remaining=remaining,
        triage_page=triage_page,
    )
    def do_triage() -> Job | None:
        triage_job = enqueue_page_triage(db, doc, page=verdict.page, precompute=True)
        return ingest_job or triage_job

    def do_cook() -> Job | None:
        generated = count_assertions_on_page(
            db, document_id, verdict.page, serve_mode=verdict.cook_mode
        )
        budget = get_question_budget(doc, verdict.page, mode=verdict.cook_mode)
        batch = plan_refill_batch(remaining=budget - generated)
        return apply(
            evaluate_refill_dispatch(batch=batch),
            {
                "skip": lambda: ingest_job,
                "enqueue": lambda: enqueue_page_batch(
                    db,
                    doc,
                    page=verdict.page,
                    batch_size=batch,
                    start_sequence=generated,
                    cook_mode=verdict.cook_mode,
                )
                or ingest_job,
            },
        )

    return apply(
        evaluate_newspaper_edition_dispatch(
            action=verdict.action, page=verdict.page
        ),
        {"triage": do_triage, "cook": do_cook, "ingest_only": lambda: ingest_job},
    )


def enqueue_page_triage(db: Session, doc: Document, *, page: int, precompute: bool = False) -> Job:
    # Eager precompute triage for non-current pages must not touch the doc-level
    # generation_pending flag (which tracks only the current page's generation).
    pick(
        not precompute,
        lambda: save_progress(db, doc, {"generation_pending": True}),
        lambda: None,
    )

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
    cook_mode: str = "learn",
) -> Job | None:
    from app.services.question_budget import parse_budget_mode

    mode = parse_budget_mode(cook_mode)
    generated = count_assertions_on_page(db, doc.id, page, serve_mode=mode)
    start_sequence = max(start_sequence, generated)
    progress = get_progress(doc)
    budget = get_question_budget(doc, page, mode=mode)
    remaining = budget - generated
    current_page = int(progress.get("current_page") or page_range_bounds(doc)[0])
    is_current_page = page == current_page
    active = _has_active_generate_job_for_page(db, doc.id, page)
    sized = min(batch_size, remaining, MAX_GENERATE_BATCH_SIZE)

    def clear_pending() -> None:
        save_progress(db, doc, {"generation_pending": False})
        db.commit()
        return None

    def mark_pending() -> None:
        save_progress(db, doc, {"generation_pending": True})
        db.commit()
        return None

    def do_enqueue() -> Job:
        _cancel_queued_generate_jobs_for_page(db, doc.id, page)
        pick(
            is_current_page,
            lambda: save_progress(db, doc, {"generation_pending": True}),
            lambda: None,
        )
        activity_id = create_activity(
            db,
            type_uri="/vocab/activity/generate_questions",
            agent="question_pool.page_batch",
            source_slug="user-upload",
            stats={
                "artifact_id": str(doc.id),
                "page_number": page,
                "batch_size": sized,
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
                "batch_size": sized,
                "start_sequence": start_sequence,
                "cook_mode": mode,
            },
        )
        db.commit()
        return job

    return apply(
        evaluate_page_batch_enqueue(
            remaining=remaining,
            is_current=is_current_page,
            active=active,
        ),
        {
            "clear_pending": clear_pending,
            "skip": lambda: None,
            "mark_pending": mark_pending,
            "enqueue": do_enqueue,
        },
    )


def _initial_pool_target(doc: Document, page: int) -> int:
    return min(INITIAL_BATCH_SIZE, get_question_budget(doc, page))


def _maybe_enqueue_initial_pool_remainder(
    db: Session, doc: Document, *, page: int
) -> Job | None:
    """After the first question lands, fill the warm pool up to INITIAL_BATCH_SIZE."""
    generated = count_assertions_on_page(db, doc.id, page)
    target = _initial_pool_target(doc, page)
    remaining = plan_refill_batch(remaining=max(0, target - generated))
    return apply(
        evaluate_refill_dispatch(
            batch=pick(
                generated >= target or _has_active_generate_job_for_page(db, doc.id, page),
                lambda: 0,
                lambda: remaining,
            )
        ),
        {
            "skip": lambda: None,
            "enqueue": lambda: enqueue_page_batch(
                db,
                doc,
                page=page,
                batch_size=remaining,
                start_sequence=generated,
            ),
        },
    )


def _enqueue_first_question_batch(
    db: Session, doc: Document, *, page: int
) -> Job | None:
    generated = count_assertions_on_page(db, doc.id, page)
    return pick(
        generated > 0,
        lambda: _maybe_enqueue_initial_pool_remainder(db, doc, page=page),
        lambda: _enqueue_fresh_first_batch(db, doc, page=page),
    )


def _enqueue_fresh_first_batch(db: Session, doc: Document, *, page: int) -> Job | None:
    from app.services.session_design import plan_first_cook_batch

    budget = get_question_budget(doc, page)
    batch = plan_first_cook_batch(budget=budget, first_batch=FIRST_QUESTION_BATCH_SIZE)
    return pick(
        batch <= 0,
        lambda: None,
        lambda: enqueue_page_batch(db, doc, page=page, batch_size=batch, start_sequence=0),
    )


def maybe_enqueue_early_page_triage(
    db: Session, document_id: uuid.UUID, *, page_number: int
) -> Job | None:
    """Overlap triage with RAG page ingest so Learn does not wait on triage after ready."""
    doc = db.get(Document, document_id)

    def maybe_enqueue() -> Job | None:
        meta = dict(doc.meta or {})
        page_from, _ = page_range_bounds(doc)

        def persist_init() -> None:
            progress = get_progress(doc)
            progress.setdefault("current_page", page_from)
            meta["question_progress"] = progress
            doc.meta = meta
            flag_modified(doc, "meta")
            db.commit()

        def after_guards() -> Job | None:
            pick(not meta.get("question_pool_initialized"), persist_init, lambda: None)
            return enqueue_page_triage(db, doc, page=page_number)

        return pick(
            meta.get("no_searchable_text")
            or not meta.get("selected_range")
            or page_number != page_from
            or bool(get_page_coverage(doc, page_number))
            or _has_active_generate_job_for_page(db, document_id, page_number),
            lambda: None,
            after_guards,
        )

    return pick(_missing(doc), lambda: None, maybe_enqueue)


def on_triage_completed(
    db: Session, document_id: uuid.UUID, *, page: int, precompute: bool = False
) -> Job | None:
    doc = db.get(Document, document_id)

    def newspaper_precompute() -> Job | None:
        from app.services.session_design import evaluate_background_first_batch

        budget = get_question_budget(doc, page)
        generated = count_assertions_on_page(db, document_id, page)
        batch = evaluate_background_first_batch(
            budget=budget,
            generated=generated,
            first_batch=FIRST_QUESTION_BATCH_SIZE,
        )

        def with_batch() -> Job | None:
            job = enqueue_page_batch(
                db, doc, page=page, batch_size=batch, start_sequence=0
            )
            _enqueue_newspaper_edition_triage(db, doc)
            return job

        def without_batch() -> None:
            _enqueue_newspaper_edition_triage(db, doc)
            return None

        return pick(batch > 0, with_batch, without_batch)

    def background_precompute() -> Job | None:
        from app.services.background_prep import is_background_prep, on_background_triage_completed

        return pick(
            is_background_prep(doc),
            lambda: on_background_triage_completed(db, document_id, page=page),
            lambda: None,
        )

    def precompute_path() -> Job | None:
        return pick(_is_newspaper_doc(doc), newspaper_precompute, background_precompute)

    def spawn_aux_and_ready() -> None:
        _maybe_spawn_coding(db, doc, page=page)
        _maybe_spawn_debug(db, doc, page=page)
        from app.services.newspaper import maybe_mark_newspaper_edition_ready

        maybe_mark_newspaper_edition_ready(db, doc)

    def current_path() -> Job | None:
        progress = get_progress(doc)

        def other_page() -> None:
            save_progress(db, doc, {"generation_pending": False})
            db.commit()
            return None

        def generate_path() -> Job | None:
            save_progress(db, doc, {"generation_pending": False})
            db.commit()
            budget = get_question_budget(doc, page)

            def first_batch() -> Job | None:
                from app.services.session_design import plan_first_cook_batch

                batch = plan_first_cook_batch(
                    budget=budget, first_batch=FIRST_QUESTION_BATCH_SIZE
                )
                job = enqueue_page_batch(
                    db, doc, page=page, batch_size=batch, start_sequence=0
                )
                _maybe_spawn_coding(db, doc, page=page)
                _maybe_spawn_debug(db, doc, page=page)
                return job

            return pick(
                budget <= 0 or count_assertions_on_page(db, document_id, page) > 0,
                lambda: (spawn_aux_and_ready() or None),
                first_batch,
            )

        return pick(
            int(progress.get("current_page") or 0) != page,
            other_page,
            generate_path,
        )

    return pick(
        _missing(doc),
        lambda: None,
        lambda: pick(precompute, precompute_path, current_path),
    )


def _maybe_spawn_debug(db: Session, doc: Document, *, page: int) -> None:
    """Spawn debug diagnostics cook when triage flagged the page as debuggable."""
    from app.services.session_design import evaluate_auxiliary_cook_spawn

    coverage = get_page_coverage(doc, page)
    plan = evaluate_auxiliary_cook_spawn(
        programmable=bool(coverage.get("programmable")),
        debuggable=bool(coverage.get("debuggable")),
    )
    pick(
        not plan.spawn_debug,
        lambda: None,
        lambda: _try_enqueue_debug(db, doc, page=page),
    )


def _try_enqueue_debug(db: Session, doc: Document, *, page: int) -> None:
    try:
        from app.services.question_generation import enqueue_debug_generation_for_page

        enqueue_debug_generation_for_page(
            db, doc.id, page=page, account_id=doc.account_id
        )
    except Exception:
        logger.warning(
            "debug generation enqueue failed for doc=%s page=%s",
            doc.id, page, exc_info=True,
        )


def _maybe_spawn_coding(db: Session, doc: Document, *, page: int) -> None:
    """Spawn coding generation when triage flagged the page as programmable.

    Best-effort: a failure here (e.g. job table temporarily unavailable) must not
    roll back the MCQ batch enqueue that just happened. The feature degrades to
    'no coding problems for this page' silently.
    """
    from app.services.session_design import evaluate_auxiliary_cook_spawn

    coverage = get_page_coverage(doc, page)
    plan = evaluate_auxiliary_cook_spawn(
        programmable=bool(coverage.get("programmable")),
        debuggable=bool(coverage.get("debuggable")),
    )
    pick(
        not plan.spawn_coding,
        lambda: None,
        lambda: _try_enqueue_coding(db, doc, page=page),
    )


def _try_enqueue_coding(db: Session, doc: Document, *, page: int) -> None:
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

    def init_pool() -> Job | None:
        meta = dict(doc.meta or {})
        return pick(
            bool(meta.get("no_searchable_text")) or bool(meta.get("question_pool_initialized")),
            lambda: None,
            lambda: _commit_initial_pool(db, document_id, doc, meta),
        )

    return pick(
        _missing(doc) or doc.status != "ready",
        lambda: None,
        init_pool,
    )


def _commit_initial_pool(
    db: Session, document_id: uuid.UUID, doc: Document, meta: dict[str, Any]
) -> Job | None:
    page_from, _ = page_range_bounds(doc)
    progress = default_progress(doc)
    progress["current_page"] = page_from
    meta["question_pool_initialized"] = True
    meta["question_progress"] = progress
    doc.meta = meta
    flag_modified(doc, "meta")
    db.commit()

    def with_coverage() -> Job | None:
        return _enqueue_first_question_batch(db, doc, page=page_from)

    def without_coverage() -> Job | None:
        enqueue_page_triage(db, doc, page=page_from)
        return _enqueue_first_question_batch(db, doc, page=page_from)

    job = pick(bool(get_page_coverage(doc, page_from)), with_coverage, without_coverage)

    def newspaper_follow() -> None:
        _enqueue_newspaper_edition_triage(db, doc)
        _maybe_enqueue_newspaper_missing_pages(db, document_id, doc)

    pick(
        _is_newspaper_doc(doc),
        newspaper_follow,
        lambda: _enqueue_eager_triage_lookahead(db, doc, from_page=page_from),
    )
    return job


def enqueue_initial_pool_for_background_prep(db: Session, document_id: uuid.UUID) -> Job | None:
    """Initialize learn state for background prep without blocking the learner UI."""
    doc = db.get(Document, document_id)

    def init_bg() -> None:
        meta = dict(doc.meta or {})
        return pick(
            bool(meta.get("no_searchable_text")) or bool(meta.get("question_pool_initialized")),
            lambda: None,
            lambda: _commit_background_prep(db, doc, meta),
        )

    return pick(_missing(doc), lambda: None, init_bg)


def _commit_background_prep(db: Session, doc: Document, meta: dict[str, Any]) -> None:
    page_from, _ = page_range_bounds(doc)
    progress = default_progress(doc)
    progress["current_page"] = page_from
    progress["generation_pending"] = False
    meta["question_pool_initialized"] = True
    meta["question_progress"] = progress
    doc.meta = meta
    flag_modified(doc, "meta")
    db.commit()
    return None


def reset_for_new_page_range(db: Session, doc: Document, selected: dict[str, Any]) -> None:
    """Clear study progress so a new page-range selection starts fresh."""
    pages_raw = selected.get("pages")

    def from_list() -> tuple[int, int, dict[str, Any]]:
        study_pages = sorted({int(p) for p in filter(lambda p: int(p) >= 1, pages_raw)})
        return study_pages[0], study_pages[-1], {
            "from": study_pages[0],
            "to": study_pages[-1],
            "pages": study_pages,
        }

    def from_range() -> tuple[int, int, dict[str, Any]]:
        page_from = int(selected["from"])
        page_to = int(selected["to"])
        return page_from, page_to, {"from": page_from, "to": page_to}

    page_from, page_to, range_meta = pick(
        isinstance(pages_raw, list) and bool(pages_raw), from_list, from_range
    )
    progress = default_progress(doc)
    progress["current_page"] = page_from
    meta = dict(doc.meta or {})
    meta["selected_range"] = range_meta
    meta.pop("question_pool_initialized", None)
    meta.pop("rag_window", None)
    meta.pop("rag_window_ready", None)
    meta.pop("prep_mode", None)
    meta.pop("prep_phase", None)
    meta.pop("prep_complete", None)
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

    pick(
        bool(doc.account_id),
        lambda: workspace_repo.upsert_workspace(
            db,
            account_id=doc.account_id,
            artifact_id=doc.id,
            artifact_captured_at=doc.artifact_captured_at or doc.created_at,
            current_page=page_from,
            status="indexing",
            selected_range=meta["selected_range"],
            pool_available_count=0,
            pool_target=REFILL_BATCH_SIZE,
        ),
        lambda: None,
    )


def on_batch_completed(db: Session, document_id: uuid.UUID, *, page: int, saved: int) -> None:
    doc = db.get(Document, document_id)

    def complete() -> None:
        progress = get_progress(doc)
        patch: dict[str, Any] = {"generation_pending": False}
        current_page = int(progress.get("current_page") or 0)
        pick(
            current_page == page,
            lambda: patch.__setitem__(
                "generated_on_page", count_assertions_on_page(db, document_id, page)
            ),
            lambda: None,
        )
        save_progress(db, doc, patch)
        db.commit()

        def try_coach() -> None:
            try:
                from app.services.jobs import enqueue_coach_page

                enqueue_coach_page(
                    db, document_id=document_id, page=page, account_id=doc.account_id
                )
            except Exception:
                logger.warning(
                    "coach enqueue failed for doc=%s page=%s",
                    document_id,
                    page,
                    exc_info=True,
                )

        pick(bool(saved), try_coach, lambda: None)

        def newspaper_refill() -> None:
            maybe_refill_pool(db, document_id)

        def other_refill() -> None:
            from app.services.background_prep import (
                is_background_prep,
                maybe_complete_prep,
                tick_background_cook,
            )

            def bg() -> None:
                tick_background_cook(db, document_id)
                maybe_complete_prep(db, doc)

            def current_refill() -> None:
                _maybe_enqueue_initial_pool_remainder(db, doc, page=page)
                maybe_refill_pool(db, document_id)

            pick(
                is_background_prep(doc),
                bg,
                lambda: pick(current_page == page, current_refill, lambda: None),
            )

        pick(_is_newspaper_doc(doc), newspaper_refill, other_refill)
        from app.services.newspaper import maybe_mark_newspaper_edition_ready

        maybe_mark_newspaper_edition_ready(db, doc)

    pick(_missing(doc), lambda: None, complete)


def on_batch_failed(db: Session, document_id: uuid.UUID, *, page: int) -> None:
    doc = db.get(Document, document_id)

    def recover() -> None:
        progress = get_progress(doc)
        pick(
            bool(page) and int(progress.get("current_page") or 0) == page,
            lambda: (
                save_progress(db, doc, {"generation_pending": False}),
                db.commit(),
            ),
            lambda: None,
        )
        from app.services.background_prep import is_background_prep, tick_background_cook

        def bg_tick() -> None:
            try:
                tick_background_cook(db, document_id)
            except Exception:
                logger.exception(
                    "background cook re-tick failed after batch failure doc=%s page=%s",
                    document_id,
                    page,
                )

        def newspaper_recover() -> None:
            try:
                from app.services.newspaper import maybe_mark_newspaper_edition_ready

                maybe_refill_pool(db, document_id)
                maybe_mark_newspaper_edition_ready(db, doc)
            except Exception:
                logger.exception(
                    "newspaper cook recovery failed after batch failure doc=%s page=%s",
                    document_id,
                    page,
                )

        pick(
            is_background_prep(doc),
            bg_tick,
            lambda: pick(_is_newspaper_doc(doc), newspaper_recover, lambda: None),
        )

    pick(_missing(doc), lambda: None, recover)


def mark_aspect_asked(db: Session, document_id: uuid.UUID, page: int, aspect_key: str) -> None:
    mark_aspects_asked(db, document_id, page, [aspect_key])


def mark_aspects_asked(
    db: Session, document_id: uuid.UUID, page: int, aspect_keys: list[str], *, cook_mode: str = "learn"
) -> None:
    """Mark multiple aspects asked in one coverage read + one progress write.

    Replaces N calls to mark_aspect_asked (each did its own get_page_coverage +
    save_progress = O(N) DB round trips) with a single batched update.
    """
    return pick(
        not aspect_keys,
        lambda: None,
        lambda: _mark_aspects_on_doc(db, document_id, page, aspect_keys, cook_mode),
    )


def _mark_aspects_on_doc(
    db: Session,
    document_id: uuid.UUID,
    page: int,
    aspect_keys: list[str],
    cook_mode: str,
) -> None:
    doc = db.get(Document, document_id)

    def mark() -> None:
        from app.services.question_budget import parse_budget_mode
        from app.services.kc_coverage import coverage_aspects_field

        mode = parse_budget_mode(cook_mode)
        entry = dict(get_page_coverage(doc, page))
        aspect_field = coverage_aspects_field(mode)
        aspects = list(entry.get(aspect_field) or entry.get("aspects") or [])
        wanted = set(aspect_keys)
        for aspect in aspects:
            pick(
                aspect.get("key") in wanted,
                lambda a=aspect: a.__setitem__("asked", True),
                lambda: None,
            )
        entry[aspect_field] = aspects
        save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})

    pick(_missing(doc), lambda: None, mark)


def bump_aspect_attempts(
    db: Session, document_id: uuid.UUID, page: int, aspect_keys: set[str] | list[str]
) -> None:
    """Record a failed generation attempt for each still-unasked aspect, and
    abandon (mark asked) one that has now failed MAX_ASPECT_ATTEMPTS times.

    Without this, an aspect generation can never satisfy (every draft rejected by
    the verifier/critic/dedup) stays unasked forever, so coverage never completes
    and the learner is stranded once every producible question is answered.
    """

    wanted = set(aspect_keys)
    return pick(
        not wanted,
        lambda: None,
        lambda: _bump_aspects_on_doc(db, document_id, page, wanted),
    )


def _bump_aspects_on_doc(
    db: Session, document_id: uuid.UUID, page: int, wanted: set[str]
) -> None:
    from app.services.aspect_discovery import should_abandon_aspect

    doc = db.get(Document, document_id)

    def bump() -> None:
        entry = dict(get_page_coverage(doc, page))
        aspects = list(entry.get("aspects") or [])
        changed = [False]

        def bump_one(aspect: dict[str, Any]) -> None:
            def apply_attempt() -> None:
                attempts = int(aspect.get("gen_attempts") or 0) + 1
                aspect["gen_attempts"] = attempts

                def abandon() -> None:
                    aspect["asked"] = True
                    aspect["abandoned"] = True

                pick(should_abandon_aspect(attempts).abandon, abandon, lambda: None)
                changed[0] = True

            pick(
                aspect.get("key") in wanted and not aspect.get("asked"),
                apply_attempt,
                lambda: None,
            )

        for aspect in aspects:
            bump_one(aspect)

        def persist() -> None:
            entry["aspects"] = aspects
            save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})
            db.commit()

        pick(changed[0], persist, lambda: None)

    pick(_missing(doc), lambda: None, bump)


def set_coverage_complete(db: Session, document_id: uuid.UUID, page: int) -> None:
    from app.services.kc_coverage import should_persist_coverage_complete

    doc = db.get(Document, document_id)

    def persist() -> None:
        entry = dict(get_page_coverage(doc, page))
        pick(
            not should_persist_coverage_complete(entry),
            lambda: None,
            lambda: (
                entry.__setitem__("coverage_complete", True),
                save_progress(db, doc, {"page_coverage": {_page_key(page): entry}}),
            ),
        )

    pick(_missing(doc), lambda: None, persist)


def clear_stale_coverage_complete(db: Session, document_id: uuid.UUID, page: int) -> bool:
    """Drop coverage_complete when triage never landed (pre-index race poison)."""
    from app.services.kc_coverage import evaluate_stale_coverage_stamp

    doc = db.get(Document, document_id)

    def clear() -> bool:
        entry = dict(get_page_coverage(doc, page))
        mcq_count = count_assertions_on_page(db, document_id, page)
        return pick(
            not evaluate_stale_coverage_stamp(entry, mcq_count=mcq_count),
            lambda: False,
            lambda: _drop_stale_stamp(db, doc, page, entry),
        )

    return pick(_missing(doc), lambda: False, clear)


def _drop_stale_stamp(
    db: Session, doc: Document, page: int, entry: dict[str, Any]
) -> bool:
    entry.pop("coverage_complete", None)

    def save_entry() -> None:
        save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})

    def pop_page() -> None:
        progress = get_progress(doc)
        coverage = dict(progress.get("page_coverage") or {})
        coverage.pop(_page_key(page), None)
        save_progress(db, doc, {"page_coverage": coverage})

    pick(bool(entry), save_entry, pop_page)
    db.commit()
    return True


def _reclaim_stale_generate_jobs(db: Session, document_id: uuid.UUID | None = None) -> int:
    """Re-queue orphaned generate.questions jobs so learn mode can recover.

    Preserves ``result`` (incl. generation checkpoint) so a resumed run can
    continue from the last saved sequence instead of redoing LLM work.

    Lease-aware: a job with a still-valid ``lease_deadline`` is never reclaimed,
    so a long, actively-heartbeating generation is not requeued while it runs
    (which would split-brain into double execution). Only jobs whose lease has
    expired — or legacy rows past the ``locked_at`` cutoff — are touched.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=GENERATE_JOB_STALE_SECONDS)
    params: dict[str, Any] = {"cutoff": cutoff}
    doc_filter = pick(
        document_id is not None,
        lambda: "AND payload->>'document_id' = :document_id",
        lambda: "",
    )
    pick(
        document_id is not None,
        lambda: params.__setitem__("document_id", str(document_id)),
        lambda: None,
    )
    result = db.execute(
        text(
            f"""
            UPDATE jobs
            SET status = 'queued',
                locked_by = NULL,
                locked_at = NULL,
                heartbeat_at = NULL,
                lease_deadline = NULL,
                error = NULL,
                updated_at = NOW()
            WHERE name = 'generate.questions'
              AND status = 'running'
              AND (
                lease_deadline IS NOT NULL AND lease_deadline < NOW()
                OR (lease_deadline IS NULL AND locked_at IS NOT NULL AND locked_at < :cutoff)
              )
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
    from app.services.session_design import plan_eager_triage_pages

    study = selected_page_list(doc)
    preview = plan_eager_triage_pages(from_page=from_page, study_pages=study)
    return pick(not preview, lambda: None, lambda: _seed_eager_triage(db, doc, from_page, study, preview))


def _seed_eager_triage(
    db: Session,
    doc: Document,
    from_page: int,
    study: list[int],
    preview: list[int],
) -> None:
    from app.services.session_design import plan_eager_triage_pages

    covered = set(filter(lambda p: get_page_coverage(doc, p), preview))
    active = set(
        filter(
            lambda p: p not in covered and _has_active_triage_job_for_page(db, doc.id, p),
            preview,
        )
    )
    for ahead_page in plan_eager_triage_pages(
        from_page=from_page,
        study_pages=study,
        covered_pages=covered,
        active_triage_pages=active,
    ):
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
    pick(
        _has_active_generate_job(db, document_id),
        lambda: None,
        lambda: _clear_pending_if_stuck(db, document_id),
    )


def _clear_pending_if_stuck(db: Session, document_id: uuid.UUID) -> None:
    doc = db.get(Document, document_id)

    def clear() -> None:
        db.refresh(doc)
        progress = get_progress(doc)
        pick(
            not progress.get("generation_pending"),
            lambda: None,
            lambda: (
                save_progress(db, doc, {"generation_pending": False}),
                db.commit(),
            ),
        )

    pick(_missing(doc), lambda: None, clear)


def kick_generation_sync(db: Session, document_id: uuid.UUID) -> None:
    """Run triage + first batch inline when the pool is empty and no worker job is active."""
    pick(
        _has_active_generate_job(db, document_id),
        lambda: None,
        lambda: _kick_ready_doc(db, document_id),
    )


def _kick_ready_doc(db: Session, document_id: uuid.UUID) -> None:
    doc = db.get(Document, document_id)

    def kick() -> None:
        db.refresh(doc)
        progress = get_progress(doc)
        pick(
            bool(next_assertion_id(db, document_id, progress)),
            lambda: None,
            lambda: _kick_page_generation(db, document_id, doc, progress),
        )

    pick(_missing(doc) or doc.status != "ready", lambda: None, kick)


def _kick_page_generation(
    db: Session, document_id: uuid.UUID, doc: Document, progress: dict[str, Any]
) -> None:
    from app.graphs.generation_graph import run_generation
    from app.services.session_design import plan_first_cook_batch

    page = int(progress.get("current_page") or page_range_bounds(doc)[0])
    pick(
        not get_page_coverage(doc, page),
        lambda: (
            run_generation(
                db,
                document_id,
                {"mode": "page_triage", "page_number": page},
            ),
            db.refresh(doc),
        ),
        lambda: None,
    )
    pick(
        count_assertions_on_page(db, document_id, page) == 0
        and not _has_active_generate_job(db, document_id),
        lambda: run_generation(
            db,
            document_id,
            {
                "mode": "page_batch",
                "page_number": page,
                "batch_size": plan_first_cook_batch(
                    budget=get_question_budget(doc, page),
                    first_batch=FIRST_QUESTION_BATCH_SIZE,
                ),
                "start_sequence": 0,
            },
        ),
        lambda: None,
    )


def _enqueue_pool_work(db: Session, document_id: uuid.UUID, doc: Document) -> Job | None:
    db.refresh(doc)
    meta = dict(doc.meta or {})
    return pick(
        bool(meta.get("no_searchable_text")),
        lambda: None,
        lambda: pick(
            _is_newspaper_doc(doc),
            lambda: _newspaper_pool_work(db, document_id, doc, meta),
            lambda: _standard_pool_work(db, document_id, doc, meta),
        ),
    )


def _newspaper_pool_work(
    db: Session, document_id: uuid.UUID, doc: Document, meta: dict[str, Any]
) -> Job | None:
    return pick(
        not meta.get("question_pool_initialized"),
        lambda: pick(
            _has_active_generate_job(db, document_id),
            lambda: None,
            lambda: enqueue_initial_pool(db, document_id),
        ),
        lambda: _maybe_refill_newspaper_edition(db, document_id, doc),
    )


def _standard_pool_work(
    db: Session, document_id: uuid.UUID, doc: Document, meta: dict[str, Any]
) -> Job | None:
    progress = get_progress(doc)
    return pick(
        not meta.get("question_pool_initialized"),
        lambda: pick(
            _has_active_generate_job(db, document_id),
            lambda: None,
            lambda: enqueue_initial_pool(db, document_id),
        ),
        lambda: _standard_page_work(db, document_id, doc, progress),
    )


def _standard_page_work(
    db: Session, document_id: uuid.UUID, doc: Document, progress: dict[str, Any]
) -> Job | None:
    page = int(progress.get("current_page") or page_range_bounds(doc)[0])
    return pick(
        _has_active_generate_job_for_page(db, document_id, page),
        lambda: None,
        lambda: pick(
            not get_page_coverage(doc, page),
            lambda: pick(
                _has_active_triage_job_for_page(db, document_id, page),
                lambda: None,
                lambda: enqueue_page_triage(db, doc, page=page),
            ),
            lambda: pick(
                count_assertions_on_page(db, document_id, page) == 0,
                lambda: _enqueue_first_question_batch(db, doc, page=page),
                lambda: None,
            ),
        ),
    )


def clear_stale_generation_pending(db: Session, doc: Document) -> None:
    release_stuck_generation(db, doc.id)


def advance_to_next_page(
    db: Session, doc: Document, *, learner_key: str | None = None
) -> Job | None:
    progress = get_progress(doc, learner_key=learner_key)
    study_pages = selected_page_list(doc)
    page = int(
        progress.get("current_page")
        or pick(bool(study_pages), lambda: study_pages[0], lambda: 1)
    )
    try:
        idx = study_pages.index(page)
    except ValueError:
        return None
    return pick(
        idx >= len(study_pages) - 1,
        lambda: None,
        lambda: _advance_from_index(db, doc, study_pages, idx, learner_key),
    )


def _advance_from_index(
    db: Session,
    doc: Document,
    study_pages: list[int],
    idx: int,
    learner_key: str | None,
) -> Job | None:
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
        learner_key=learner_key,
    )
    db.commit()

    # Re-seed the rolling triage window so pages beyond the initial lookahead are
    # understood before the reader reaches them (the transition prefetch can only
    # pre-generate a next page whose coverage already exists).
    _enqueue_eager_triage_lookahead(db, doc, from_page=new_page)

    from app.services.rag_window import is_rag_window_ready
    from app.services.session_design import (
        plan_page_advance_next,
        should_require_rag_on_page_advance,
    )

    has_coverage = bool(get_page_coverage(doc, new_page))
    generated = pick(
        has_coverage,
        lambda: count_assertions_on_page(db, doc.id, new_page),
        lambda: 0,
    )
    rag_ready = (
        not should_require_rag_on_page_advance(
            has_coverage=has_coverage, generated=generated
        )
        or is_rag_window_ready(db, doc.id, doc)
    )
    plan = plan_page_advance_next(
        has_coverage=has_coverage,
        generated=generated,
        rag_ready=rag_ready,
    )

    def cook_first() -> Job | None:
        job = _enqueue_first_question_batch(db, doc, page=new_page)
        return pick(
            job is not None,
            lambda: job,
            lambda: pick(
                not is_rag_window_ready(db, doc.id, doc),
                lambda: enqueue_rag_window(
                    db,
                    doc.id,
                    account_id=doc.account_id,
                    current_page=new_page,
                ),
                lambda: None,
            ),
        )

    return apply(
        plan.action,
        {
            "triage": lambda: enqueue_page_triage(db, doc, page=new_page),
            "cook_first_batch": cook_first,
            "ingest_rag": lambda: enqueue_rag_window(
                db,
                doc.id,
                account_id=doc.account_id,
                current_page=new_page,
            ),
            "noop": lambda: None,
        },
    )


def ensure_question_pool(db: Session, document_id: uuid.UUID) -> Job | None:
    """Kick off or recover question generation for ready documents."""
    doc = db.get(Document, document_id)
    return apply(
        evaluate_pool_wake(
            missing=_missing(doc),
            ready=bool(doc) and doc.status == "ready",
            no_searchable_text=False,
        ),
        {
            "skip": lambda: None,
            "ok": lambda: _ensure_ready_pool(db, document_id, doc),
        },
    )


def _ensure_ready_pool(db: Session, document_id: uuid.UUID, doc: Document) -> Job | None:
    db.refresh(doc)
    return apply(
        evaluate_pool_wake(
            missing=False,
            ready=True,
            no_searchable_text=bool((doc.meta or {}).get("no_searchable_text")),
        ),
        {
            "skip": lambda: None,
            "ok": lambda: _ensure_pool_work(db, document_id, doc),
        },
    )


def _ensure_pool_work(db: Session, document_id: uuid.UUID, doc: Document) -> Job | None:
    release_stuck_generation(db, document_id)
    db.refresh(doc)
    progress = get_progress(doc)
    page = int(progress.get("current_page") or page_range_bounds(doc)[0])
    clear_stale_coverage_complete(db, document_id, page)
    db.refresh(doc)
    from app.services.rag_window import is_rag_window_ready

    rag_job = pick(
        not is_rag_window_ready(db, document_id, doc),
        lambda: (
            enqueue_rag_window(
                db,
                document_id,
                account_id=doc.account_id,
                current_page=page,
            ),
            db.commit(),
        )[0],
        lambda: None,
    )
    refill_job = maybe_refill_pool(db, document_id)
    return pick(
        bool(next_assertion_id(db, document_id, progress)),
        lambda: refill_job or rag_job,
        lambda: pick(
            refill_job is None,
            lambda: _enqueue_pool_work(db, document_id, doc) or rag_job,
            lambda: refill_job or rag_job,
        ),
    )


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

    return pick(
        budget <= 0,
        lambda: False,
        lambda: evaluate_serve_schedule(
            answered_on_page=answered_on_page,
            page_budget=budget,
        ).prefetch_transition,
    )


def maybe_transition_prefetch(db: Session, document_id: uuid.UUID) -> Job | None:
    doc = db.get(Document, document_id)
    return pick(
        _missing(doc) or doc.status != "ready",
        lambda: None,
        lambda: _maybe_prefetch_ready(db, document_id, doc),
    )


def _maybe_prefetch_ready(
    db: Session, document_id: uuid.UUID, doc: Document
) -> Job | None:
    progress = get_progress(doc)
    page = int(progress.get("current_page") or 1)
    budget = get_question_budget(doc, page)
    answered_on_page = int(progress.get("answered_on_page") or 0)
    return pick(
        is_transition_prep_done(doc, page)
        or not should_transition_prefetch(answered_on_page, budget),
        lambda: None,
        lambda: enqueue_transition_prep(
            db,
            document_id,
            current_page=page,
            account_id=doc.account_id,
        ),
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
    learner_key: str | None = None,
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
    pick(
        _missing(doc),
        lambda: None,
        lambda: _save_confirmed_on_doc(
            db,
            doc,
            assertion_id,
            choice_index=choice_index,
            correct=correct,
            learner_ability=learner_ability,
            item_difficulty=item_difficulty,
            ability_se=ability_se,
            mastery_stop=mastery_stop,
            revisit_hours=revisit_hours,
            revisit_ease=revisit_ease,
            revisit_repetitions=revisit_repetitions,
            learner_key=learner_key,
        ),
    )


def _patch_if(patch: dict[str, Any], flag: bool, key: str, value: Any) -> None:
    pick(flag, lambda: patch.__setitem__(key, value), lambda: None)


def _concept_map_patch(
    patch: dict[str, Any],
    progress: dict[str, Any],
    concept_key: Any,
    src_key: str,
    dest_key: str,
    value: Any,
) -> None:
    def write() -> None:
        mapped = dict(progress.get(src_key) or {})
        mapped[str(concept_key)] = value
        patch[dest_key] = mapped

    pick(bool(concept_key), write, lambda: None)


def _save_confirmed_on_doc(
    db: Session,
    doc: Document,
    assertion_id: uuid.UUID,
    *,
    choice_index: int,
    correct: bool,
    learner_ability: float | None,
    item_difficulty: float | None,
    ability_se: float | None,
    mastery_stop: bool | None,
    revisit_hours: float | None,
    revisit_ease: float | None,
    revisit_repetitions: int | None,
    learner_key: str | None,
) -> None:
    concept_key = db.execute(
        text("SELECT payload->>'primary_concept_key' FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).scalar()
    progress = get_progress(doc, learner_key=learner_key)
    patch: dict[str, Any] = {
        "last_confirmed_answer": {
            "assertion_id": str(assertion_id),
            "choice_index": int(choice_index),
            "correct": bool(correct),
            "concept_key": concept_key,
        },
        "session_items_answered": int(progress.get("session_items_answered") or 0) + 1,
    }
    _patch_if(patch, learner_ability is not None, "learner_ability", float(learner_ability or 0))
    _patch_if(patch, ability_se is not None, "learner_ability_se", float(ability_se or 0))
    _patch_if(patch, mastery_stop is not None, "mastery_stop", bool(mastery_stop))
    pick(
        revisit_hours is not None,
        lambda: (
            patch.__setitem__("revisit_due_hours", float(revisit_hours)),
            _concept_map_patch(
                patch,
                progress,
                concept_key,
                "concept_revisit_hours",
                "concept_revisit_hours",
                float(revisit_hours),
            ),
        ),
        lambda: None,
    )
    pick(
        revisit_ease is not None,
        lambda: (
            patch.__setitem__("revisit_ease", float(revisit_ease)),
            _concept_map_patch(
                patch,
                progress,
                concept_key,
                "concept_revisit_ease",
                "concept_revisit_ease",
                float(revisit_ease),
            ),
        ),
        lambda: None,
    )
    pick(
        revisit_repetitions is not None,
        lambda: (
            patch.__setitem__("revisit_repetitions", int(revisit_repetitions)),
            _concept_map_patch(
                patch,
                progress,
                concept_key,
                "concept_revisit_repetitions",
                "concept_revisit_repetitions",
                int(revisit_repetitions),
            ),
        ),
        lambda: None,
    )

    def update_concept_ability() -> None:
        from app.services.calibration_engine import DEFAULT_RATING, update_from_outcome

        fresh = get_progress(doc, learner_key=learner_key)
        concept_ability = dict(fresh.get("concept_ability") or {})
        concept_n = dict(fresh.get("concept_ability_n") or {})
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

    pick(
        bool(concept_key) and item_difficulty is not None,
        update_concept_ability,
        lambda: None,
    )
    save_progress(db, doc, patch, learner_key=learner_key)
    db.commit()


def record_answer(
    db: Session,
    document_id: uuid.UUID,
    assertion_id: uuid.UUID,
    *,
    learner_key: str | None = None,
) -> None:
    doc = db.get(Document, document_id)
    pick(
        _missing(doc),
        lambda: None,
        lambda: _record_answer_on_doc(db, document_id, doc, assertion_id, learner_key),
    )


def _record_answer_on_doc(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    assertion_id: uuid.UUID,
    learner_key: str | None,
) -> None:
    from app.services.newspaper import is_newspaper_document
    from app.services.question_pool import (
        answered_ids_storage_key,
        mode_answered_ids,
        serve_budget_mode,
    )

    progress = get_progress(doc, learner_key=learner_key)
    aid = str(assertion_id)
    newspaper = is_newspaper_document(doc)
    serve_mode = serve_budget_mode(doc, learner_key=learner_key)
    storage_key, answered = pick(
        newspaper,
        lambda: (answered_ids_storage_key(serve_mode), list(mode_answered_ids(progress, serve_mode))),
        lambda: ("answered_ids", [str(x) for x in progress.get("answered_ids") or []]),
    )
    pick(
        aid in answered,
        lambda: None,
        lambda: _append_recorded_answer(
            db,
            document_id,
            doc,
            assertion_id,
            learner_key,
            progress,
            aid,
            newspaper,
            serve_mode,
            storage_key,
            answered,
        ),
    )


def _append_recorded_answer(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    assertion_id: uuid.UUID,
    learner_key: str | None,
    progress: dict[str, Any],
    aid: str,
    newspaper: bool,
    serve_mode: Mode,
    storage_key: str,
    answered: list[str],
) -> None:
    from app.services.question_pool import edition_assertion_ids
    from app.services.document_learner_state import document_uses_learner_overlay

    answered.append(aid)
    patch: dict[str, Any] = {
        storage_key: answered,
        "answered_ids": answered,
        "answered_on_page": int(progress.get("answered_on_page") or 0) + 1,
    }

    def maybe_learn_complete() -> None:
        from app.services.session_design import evaluate_newspaper_learn_complete

        learn_ids = edition_assertion_ids(db, document_id, doc, serve_mode="learn")
        pick(
            evaluate_newspaper_learn_complete(
                learn_pool_count=len(learn_ids),
                all_learn_answered=bool(learn_ids)
                and all(row_id in answered for row_id in learn_ids),
                generation_pending=False,
            ),
            lambda: patch.__setitem__("learn_complete", True),
            lambda: None,
        )

    pick(newspaper and serve_mode == "learn", maybe_learn_complete, lambda: None)
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

    def mark_coverage() -> None:
        from app.services.kc_coverage import mark_aspects_answered

        page_num = int(row["page"])
        entry = dict(get_page_coverage(doc, page_num))
        entry["aspects"] = mark_aspects_answered(entry.get("aspects") or [], str(row["key"]))
        patch["page_coverage"] = {_page_key(page_num): entry}

    pick(
        bool(row and row.get("key") and row.get("page") and not document_uses_learner_overlay(doc)),
        mark_coverage,
        lambda: None,
    )
    save_progress(db, doc, patch, learner_key=learner_key)
    db.commit()
    maybe_refill_pool(db, document_id)
    maybe_transition_prefetch(db, document_id)


def maybe_refill_pool(db: Session, document_id: uuid.UUID) -> Job | None:
    doc = db.get(Document, document_id)
    return pick(
        _missing(doc) or doc.status != "ready",
        lambda: None,
        lambda: pick(
            _is_newspaper_doc(doc),
            lambda: _maybe_refill_newspaper_edition(db, document_id, doc),
            lambda: _maybe_refill_standard(db, document_id, doc),
        ),
    )


def _maybe_refill_standard(
    db: Session, document_id: uuid.UUID, doc: Document
) -> Job | None:
    progress = get_progress(doc)
    page = int(progress.get("current_page") or 1)
    # Never bail solely on doc-level generation_pending — that flag is also set by
    # current-page work and used to freeze refill while a batch ran, so a fast
    # learner drained the pool with no follow-up job queued. Per-page active-job
    # check respects the same-page lock (no duplicate concurrent batches) while
    # still allowing refill when only another page's prefetch set the flag.
    return pick(
        _has_active_generate_job_for_page(db, document_id, page)
        or not get_page_coverage(doc, page),
        lambda: None,
        lambda: _maybe_enqueue_refill_batch(db, document_id, doc, progress, page),
    )


def _maybe_enqueue_refill_batch(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    progress: dict[str, Any],
    page: int,
) -> Job | None:
    budget = effective_question_budget(doc, page, progress)
    generated_on_page = count_assertions_on_page(db, document_id, page)
    answered_ids = [str(x) for x in progress.get("answered_ids") or []]
    answered_on_page = count_answered_on_page(db, document_id, page, answered_ids)
    available = _count_available(db, doc.id, progress)

    from app.services.question_budget import evaluate_generation_stop
    from app.services.session_design import evaluate_serve_schedule

    sched = evaluate_serve_schedule(
        ready_count=available,
        answered_on_page=answered_on_page,
    )
    remaining = budget - generated_on_page
    batch = plan_refill_batch(remaining=remaining)
    return pick(
        evaluate_generation_stop(
            generated=generated_on_page,
            budget=budget,
            coverage_complete=is_coverage_complete(doc, page),
        ),
        lambda: None,
        lambda: pick(
            (sched.refill_now or sched.periodic_refill) and batch > 0,
            lambda: enqueue_page_batch(
                db,
                doc,
                page=page,
                batch_size=batch,
                start_sequence=generated_on_page,
            ),
            lambda: None,
        ),
    )
