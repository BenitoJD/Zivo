"""Rolling per-page question pool — agent budget, prefetch batches, page advance."""

from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Document
from app.services.document_learn_state import load_progress as load_learn_state_row
from app.services.document_learn_state import save_progress_row
from app.services.mcq_assertion_facets import page_assertion_ids_from_facets
from app.repositories import workspace as workspace_repo

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
ABSOLUTE_MAX_QUESTIONS_PER_PAGE = int(os.getenv("ZIVO_MAX_QUESTIONS_PER_PAGE", "150"))
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


def get_study_mode(doc: Document) -> str:
    """The learner's per-document choice: 'adaptive' (difficulty_edge) or 'classic'
    (sequence). Falls back to the global default policy when unset."""
    from app.config import get_settings

    policy = (
        str(get_progress(doc).get("selection_policy") or "").strip().lower()
        or (get_settings().selection_policy or "sequence").lower()
    )
    return "classic" if policy == "sequence" else "adaptive"


def set_study_mode(db: Session, doc: Document, mode: str) -> str:
    """Persist the learner's Adaptive/Classic choice for this document and return it.
    Both policies run over the same question pool, so no regeneration is needed."""
    policy = "sequence" if str(mode).strip().lower() == "classic" else "difficulty_edge"
    save_progress(db, doc, {"selection_policy": policy})
    return "classic" if policy == "sequence" else "adaptive"


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
    # No floor: the budget is exactly what triage decided (the count of distinct
    # testable ideas), capped only at the absolute max. A missing budget means
    # triage hasn't landed yet — seed a small speculative batch so generation can
    # start, but a triaged value of 0 is honoured (distinguish missing from 0).
    raw = cov.get("question_budget")
    budget = int(raw) if raw is not None else INITIAL_BATCH_SIZE
    return max(0, min(ABSOLUTE_MAX_QUESTIONS_PER_PAGE, budget))


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

    # Per-document override (the learner's Adaptive/Classic choice) wins; otherwise
    # fall back to the global default policy.
    policy = (
        str(progress.get("selection_policy") or "").strip().lower()
        or (get_settings().selection_policy or "sequence").lower()
    )
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
        "study_mode": get_study_mode(doc),
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


# --- Job orchestration lives in question_pool_jobs (keeps this module focused on
# core state + selection). Re-exported here so external code keeps one import
# surface. The import is at the bottom so the core names above are defined before
# question_pool_jobs imports them back (resolves the layering, no cycle). ---
from app.services.question_pool_jobs import (  # noqa: E402,F401
    _cancel_queued_generate_jobs,
    _cancel_queued_generate_jobs_for_page,
    _enqueue_eager_triage_lookahead,
    _enqueue_first_question_batch,
    _enqueue_pool_work,
    _has_active_generate_job,
    _has_active_generate_job_for_page,
    _has_active_triage_job_for_page,
    _initial_pool_target,
    _maybe_enqueue_initial_pool_remainder,
    _reclaim_stale_generate_jobs,
    advance_to_next_page,
    clear_stale_coverage_complete,
    clear_stale_generation_pending,
    enqueue_initial_pool,
    enqueue_page_batch,
    enqueue_page_triage,
    ensure_question_pool,
    is_transition_prep_done,
    kick_generation_sync,
    mark_aspect_asked,
    mark_aspects_asked,
    mark_transition_prep_done,
    maybe_enqueue_early_page_triage,
    maybe_refill_pool,
    maybe_transition_prefetch,
    on_batch_completed,
    on_batch_failed,
    on_triage_completed,
    record_answer,
    release_stuck_generation,
    reset_for_new_page_range,
    save_confirmed_answer,
    set_coverage_complete,
    should_transition_prefetch,
)
