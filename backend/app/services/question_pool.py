"""Rolling per-page question pool — agent budget, prefetch batches, page advance."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import Document, Job
from app.repositories.intel import create_activity
from app.services.document_learn_state import load_progress as load_learn_state_row
from app.services.document_learn_state import save_progress_row
from app.services.mcq_assertion_facets import page_assertion_ids_from_facets
from app.repositories import workspace as workspace_repo
from app.services.jobs import enqueue_generate, enqueue_rag_window, enqueue_transition_prep

INITIAL_BATCH_SIZE = 5
# The first batch after triage fills the whole warm pool in one LLM call
# (generate_quality_mcq_batch produces N MCQs per call), so a batch of 5 takes
# ~the same wall-clock as a batch of 1 — generating just one question first and
# the remaining four in a second job cycle only added a queue handoff + wait.
FIRST_QUESTION_BATCH_SIZE = INITIAL_BATCH_SIZE
REFILL_BATCH_SIZE = 5
REFILL_AFTER_ANSWERED = 2
# Proactive low-water mark: keep refilling so the ready buffer never silently
# drains to empty before the next question is needed. The moment the count of
# ready, unanswered questions dips below this, a refill batch is staged — instead
# of only topping up every REFILL_AFTER_ANSWERED answers or once fully drained.
# This is what keeps a fast solver from ever catching up to an empty pool.
READY_LOW_WATER = int(os.getenv("ZIVO_READY_LOW_WATER", "6"))
TRANSITION_PREFETCH_RATIO = float(os.getenv("ZIVO_TRANSITION_PREFETCH_RATIO", "0.70"))
TRANSITION_GENERATION_RATIO = float(os.getenv("ZIVO_TRANSITION_GENERATION_RATIO", "0.30"))
GENERATION_AHEAD_BUFFER = int(os.getenv("ZIVO_GENERATION_AHEAD_BUFFER", "12"))
# Eagerly triage the current page + this many pages ahead at init, so the document
# is understood before the reader arrives. Triage only (cheap, ~1 call/page);
# batches stay on-demand + next-page prefetch, so we don't burn tokens generating
# pages the reader never reaches. 3 is enough to stay ahead of the transition
# prefetch (which fires at 40% page progress) without triaging pages that are
# never opened — 10 was pure waste (~7 unused triage calls/doc).
EAGER_TRIAGE_LOOKAHEAD = int(os.getenv("ZIVO_EAGER_TRIAGE_LOOKAHEAD", "3"))
ABSOLUTE_MAX_QUESTIONS_PER_PAGE = int(os.getenv("ZIVO_MAX_QUESTIONS_PER_PAGE", "40"))
# Backward-compatible alias for API consumers
MAX_QUESTIONS_PER_PAGE = ABSOLUTE_MAX_QUESTIONS_PER_PAGE
# A generate job left in 'running' after a worker crash blocks recovery until reclaimed.
GENERATE_JOB_STALE_SECONDS = 180


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
    progress: dict[str, Any] | None = None
    if isinstance(doc, Document):
        session = Session.object_session(doc)
        if session is not None:
            try:
                progress = load_learn_state_row(session, doc.id)
            except Exception:
                progress = None
    if not isinstance(progress, dict):
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
    db.refresh(doc, with_for_update=True)
    merged = _merge_progress(get_progress(doc), progress)
    save_progress_row(db, doc.id, merged)
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
    aspect_dedup: dict[str, Any] | None = None,
    content_type: str | None = None,
    non_content: bool = False,
) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    entry: dict[str, Any] = {
        "question_budget": question_budget,
        "aspects": aspects,
        # A deliberate zero-question verdict counts as complete immediately —
        # distinct from "no aspects yet" (a pre-index race), which is not.
        "coverage_complete": bool(non_content),
        "rationale": rationale,
        "triage_activity_id": triage_activity_id,
        "content_type": content_type,
        "non_content": bool(non_content),
    }
    if aspect_dedup:
        entry["aspect_dedup"] = aspect_dedup
    save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})
    db.commit()
    db.refresh(doc)


def is_non_content_page(doc: Document, page: int) -> bool:
    """True when triage deliberately judged this page to have zero testable content."""
    return bool(get_page_coverage(doc, page).get("non_content"))


def get_question_budget(doc: Document, page: int) -> int:
    cov = get_page_coverage(doc, page)
    # A deliberate zero verdict is honoured exactly (no floor) — the whole point
    # of open-world: ask nothing on a cover page rather than force filler.
    if cov.get("non_content"):
        return 0
    budget = int(cov.get("question_budget") or INITIAL_BATCH_SIZE)
    return max(INITIAL_BATCH_SIZE, min(ABSOLUTE_MAX_QUESTIONS_PER_PAGE, budget))


def effective_question_budget(
    doc: Document,
    page: int,
    progress: dict[str, Any] | None = None,
) -> int:
    """Cap generation ahead of observed learner consumption."""
    cap = get_question_budget(doc, page)
    if progress is None:
        return cap
    answered = int(progress.get("answered_on_page") or 0)
    if answered <= 0:
        return min(cap, max(INITIAL_BATCH_SIZE * 2, GENERATION_AHEAD_BUFFER))
    return min(cap, answered + GENERATION_AHEAD_BUFFER)


def is_coverage_complete(doc: Document, page: int) -> bool:
    cov = get_page_coverage(doc, page)
    # A deliberate zero-question verdict is complete the moment triage lands —
    # empty aspects here mean "nothing to ask", not "not triaged yet".
    if cov.get("non_content"):
        return True
    aspects = cov.get("aspects") or []
    if not aspects:
        # coverage_complete without triage aspects is stale (e.g. pre-index race).
        return False
    if cov.get("coverage_complete"):
        return True
    return all(a.get("asked") for a in aspects)


def count_assertions_on_page(db: Session, document_id: uuid.UUID, page: int) -> int:
    return len(page_assertion_ids(db, document_id, page))


def page_assertion_ids(db: Session, document_id: uuid.UUID, page: int) -> list[str]:
    """Ordered assertion ids for a page — facet table first, JSONB fallback."""
    from_facets = page_assertion_ids_from_facets(db, document_id, page)
    if from_facets:
        return from_facets
    return list(
        db.execute(
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


def _count_available(
    db: Session,
    document_id: uuid.UUID,
    progress: dict[str, Any],
    *,
    page_ids: list[str] | None = None,
) -> int:
    page = int(progress.get("current_page") or 1)
    answered = {str(x) for x in progress.get("answered_ids") or []}
    rows = page_ids if page_ids is not None else page_assertion_ids(db, document_id, page)
    return sum(1 for row_id in rows if row_id not in answered)


def _concept_keys_for_ids(
    db: Session, ids: list[str]
) -> dict[str, str | None]:
    """Map assertion id -> primary_concept_key for the given ids."""
    if not ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT id::text AS id, payload->>'primary_concept_key' AS key
            FROM intel.assertion
            WHERE id::text = ANY(:ids)
            """
        ),
        {"ids": ids},
    ).mappings().all()
    return {r["id"]: r["key"] for r in rows}


def _difficulty_for_ids(db: Session, ids: list[str]) -> dict[str, float]:
    """Map assertion id -> latest calibrated difficulty for the given ids.

    One indexed point-read per item (DISTINCT ON newest as_of). Items with no
    calibration row yet are simply absent — the difficulty_edge policy treats them
    as neutral and degrades to sequence order when none are calibrated.
    """
    if not ids:
        return {}
    from app.repositories.intel import concept_id
    from app.services.calibration import DIFFICULTY_PROJECTION_URI

    rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (subject_assertion_id)
                   subject_assertion_id::text AS id, value->>'rating' AS rating
            FROM intel.projection
            WHERE type_concept_id = :type_id
              AND subject_assertion_id::text = ANY(:ids)
            ORDER BY subject_assertion_id, as_of DESC
            """
        ),
        {"type_id": concept_id(db, DIFFICULTY_PROJECTION_URI), "ids": ids},
    ).mappings().all()
    out: dict[str, float] = {}
    for r in rows:
        try:
            out[r["id"]] = float(r["rating"])
        except (TypeError, ValueError):
            continue
    return out


def select_next_assertion(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    progress: dict[str, Any],
    *,
    page_ids: list[str] | None = None,
) -> str | None:
    """The loop's choosing step — swappable policy over the unanswered candidates.

    Default ("sequence") returns the first unanswered question, identical to the
    legacy behaviour. "concept_reinforce" reacts to the learner's last answer.
    Candidates stay in sequence order so any policy degrades safely to legacy.
    """
    page = int(progress.get("current_page") or 1)
    answered = {str(x) for x in progress.get("answered_ids") or []}
    rows = page_ids if page_ids is not None else page_assertion_ids(db, document_id, page)
    candidates = [rid for rid in rows if rid not in answered]
    if not candidates:
        return None

    from app.config import get_settings

    policy = (get_settings().selection_policy or "sequence").lower()
    if policy == "sequence" or len(candidates) == 1:
        return candidates[0]

    from app.services.selection import build_learner_state, choose_next_assertion

    state = build_learner_state(progress)
    # Load only the signal the active policy needs (default path stays query-free).
    difficulty_by_id: dict[str, float] = {}
    lineage_by_id: dict[str, str] = {}
    if policy == "concept_reinforce":
        concept_by_id = _concept_keys_for_ids(db, candidates)
    elif policy == "difficulty_edge":
        difficulty_by_id = _difficulty_for_ids(db, candidates)
        concept_by_id = _concept_keys_for_ids(db, candidates)  # for per-concept ability
        if state.last_assertion_id:
            lineage_by_id = _lineage_successors(db, state.last_assertion_id, candidates)
    else:
        concept_by_id = {}
    return choose_next_assertion(
        policy, candidates, concept_by_id, state, difficulty_by_id, lineage_by_id
    )


def _lineage_successors(
    db: Session, from_assertion_id: str, candidate_ids: list[str]
) -> dict[str, str]:
    """Map candidate id -> lineage link kind from the last answered assertion.

    Returns only ``follow_up_after_miss`` / ``harder_than`` edges that land on a
    current candidate, so the selector can re-approach a missed idea or advance after
    a hit. Empty (degrade to band/sequence) when no such edge exists.
    """
    if not candidate_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT al.to_assertion_id::text AS id,
                   split_part(c.uri, '/', 4) AS kind
            FROM intel.assertion_lineage al
            JOIN intel.concept c ON c.id = al.link_type_concept_id
            WHERE al.from_assertion_id::text = :from_id
              AND al.to_assertion_id::text = ANY(:ids)
              AND c.uri IN ('/vocab/link/follow_up_after_miss', '/vocab/link/harder_than')
            """
        ),
        {"from_id": str(from_assertion_id), "ids": candidate_ids},
    ).mappings().all()
    return {r["id"]: r["kind"] for r in rows}


def next_assertion_id(
    db: Session,
    document_id: uuid.UUID,
    progress: dict[str, Any],
    *,
    page_ids: list[str] | None = None,
) -> str | None:
    doc = db.get(Document, document_id)
    if doc is None:
        return None
    return select_next_assertion(db, document_id, doc, progress, page_ids=page_ids)


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


def is_page_complete(
    db: Session,
    doc: Document,
    progress: dict[str, Any],
    *,
    page_ids: list[str] | None = None,
) -> bool:
    # Non-content pages are complete by definition — never block advancement on
    # a cover page just because it produced zero questions.
    if is_non_content_page(doc, int(progress.get("current_page") or 1)):
        return True
    if progress.get("generation_pending"):
        return False
    if next_assertion_id(db, doc.id, progress, page_ids=page_ids):
        return False
    page = int(progress.get("current_page") or 1)
    budget = effective_question_budget(doc, page, progress)
    generated = len(page_ids) if page_ids is not None else count_assertions_on_page(db, doc.id, page)
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
    answered_set = set(answered_ids)
    page_ids = page_assertion_ids(db, document_id, page)
    questions_generated = len(page_ids)
    questions_answered = sum(1 for row_id in page_ids if row_id in answered_set)
    budget = effective_question_budget(doc, page, progress)
    # Loop's choosing step (policy-driven; defaults to sequence order).
    next_id = select_next_assertion(db, document_id, doc, progress, page_ids=page_ids)
    coverage_complete = is_coverage_complete(doc, page)
    page_complete = is_page_complete(db, doc, progress, page_ids=page_ids)
    non_content = is_non_content_page(doc, page)
    last_study_page = study_pages[-1] if study_pages else page_to
    document_complete = page_complete and page == last_study_page

    # Open-world empty state: surface a reason only when the whole study range
    # genuinely produced nothing to ask, so the UI can say so instead of spinning.
    no_questions_reason: str | None = None
    if document_complete and questions_generated == 0 and questions_answered == 0:
        if _study_range_has_no_questions(db, document_id, doc):
            no_questions_reason = "no_testable_content"

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
        "non_content": non_content,
        "no_questions_reason": no_questions_reason,
        "document_complete": document_complete,
        "pool_available": sum(1 for row_id in page_ids if row_id not in answered_set),
        "generated_on_page": questions_generated,
        "answered_on_page": questions_answered,
        "max_per_page": budget,
        "rag_window_pages": rag_pages,
        "rag_window_ready": rag_ready,
    }


def _study_range_has_no_questions(
    db: Session, document_id: uuid.UUID, doc: Document
) -> bool:
    """True when not a single active question exists across the selected pages."""
    pages = selected_page_list(doc)
    if not pages:
        return False
    count = db.execute(
        text(
            """
            SELECT COUNT(*)::int FROM intel.assertion
            WHERE payload->>'artifact_id' = :aid
              AND status = 'active'
              AND (payload->>'page_number')::int = ANY(:pages)
            """
        ),
        {"aid": str(document_id), "pages": [int(p) for p in pages]},
    ).scalar()
    return int(count or 0) == 0


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
    if remaining <= 0:
        save_progress(db, doc, {"generation_pending": False})
        db.commit()
        return None

    batch_size = min(batch_size, remaining)
    # Per-page guard: a different page (e.g. the next page's transition prefetch)
    # may be generating concurrently — only block on a job for THIS page.
    if _has_active_generate_job_for_page(db, doc.id, page):
        save_progress(db, doc, {"generation_pending": True})
        db.commit()
        return None

    _cancel_queued_generate_jobs_for_page(db, doc.id, page)
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
        return None
    generated = count_assertions_on_page(db, document_id, page)
    if generated > 0:
        return None
    batch = min(FIRST_QUESTION_BATCH_SIZE, budget)
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
    if current_page == page:
        _maybe_enqueue_initial_pool_remainder(db, doc, page=page)


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
    prefetch (which fires at 70% of a page) can generate the next page's pool —
    transition_prep only generates a next page whose coverage already exists.
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

    if next_assertion_id(db, document_id, progress):
        return rag_job

    # Request path is read-only: only ENQUEUE background work, never generate
    # inline. Generation runs in the parallel CPU workers (and is pre-warmed by
    # eager precompute at index-ready), so the learn-queue request returns fast
    # and the client reads from a warm pool — the ≤5s seamless guarantee.
    job: Job | None = rag_job
    if not _has_active_generate_job(db, document_id):
        pool_job = maybe_refill_pool(db, document_id)
        if pool_job is None:
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


def save_confirmed_answer(
    db: Session,
    document_id: uuid.UUID,
    assertion_id: uuid.UUID,
    *,
    choice_index: int,
    correct: bool,
    learner_ability: float | None = None,
    item_difficulty: float | None = None,
) -> None:
    """Persist the learner's latest confirmed MCQ choice for tutor chat context.

    Also records the question's concept key so the selection loop can react to the
    last answer, and mirrors the learner's freshly calibrated ability into progress so
    the difficulty_edge policy can target their edge without an extra read. When the
    item's calibrated difficulty is supplied, advances the learner's *per-concept*
    ability with one Elo step, so a learner strong in one concept and weak in another
    is met in the right band on each. ``learner_ability``/``item_difficulty`` are only
    passed for a genuinely new (non-replayed) answer, so this never double-counts.
    """
    doc = db.get(Document, document_id)
    if not doc:
        return
    concept_key = db.execute(
        text("SELECT payload->>'primary_concept_key' FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).scalar()
    patch: dict[str, Any] = {
        "last_confirmed_answer": {
            "assertion_id": str(assertion_id),
            "choice_index": int(choice_index),
            "correct": bool(correct),
            "concept_key": concept_key,
        }
    }
    if learner_ability is not None:
        patch["learner_ability"] = float(learner_ability)
    if concept_key and item_difficulty is not None:
        from app.services.calibration import DEFAULT_RATING, elo_update

        progress = get_progress(doc)
        concept_ability = dict(progress.get("concept_ability") or {})
        prior = float(concept_ability.get(concept_key, DEFAULT_RATING))
        concept_ability[concept_key] = elo_update(
            prior, float(item_difficulty), bool(correct)
        ).ability
        patch["concept_ability"] = concept_ability
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
    if progress.get("generation_pending"):
        return None

    page = int(progress.get("current_page") or 1)
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
    low_water = available < READY_LOW_WATER
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
