"""Rolling per-page question pool — agent budget, prefetch batches, page advance."""

from __future__ import annotations

import os
import uuid
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Document
from app.services.document_learn_state import load_progress as load_learn_state_row
from app.services.document_learn_state import save_progress_row
from app.services.document_learner_state import (
    learner_key_for_user,
    load_learner_progress,
    save_learner_progress_row,
)
from app.services.mcq_assertion_facets import page_assertion_ids_from_facets
from app.repositories import workspace as workspace_repo
from app.services.question_budget import (
    Mode,
    SPECULATIVE_PAGE_N,
    parse_budget_mode,
    plan_document_budget,
    plan_page_budget,
    speculative_page_budget,
    units_from_aspect_dicts,
)
from app.services import aspect_discovery as _aspect_discovery
from app.services import content_worthiness as _content_worthiness
from app.services import session_design as _session_design

# Re-export schedule / abandon / reselect thresholds for ETA / jobs / tests.
MAX_ASPECT_ATTEMPTS = _aspect_discovery.MAX_ASPECT_ATTEMPTS
EMPTY_PAGE_RESELECT_STREAK = _content_worthiness.EMPTY_PAGE_RESELECT_STREAK
EAGER_TRIAGE_LOOKAHEAD = _session_design.EAGER_TRIAGE_LOOKAHEAD
READY_LOW_WATER = _session_design.READY_LOW_WATER
TRANSITION_GENERATION_RATIO = _session_design.TRANSITION_GENERATION_RATIO
TRANSITION_PREFETCH_RATIO = _session_design.TRANSITION_PREFETCH_RATIO
REFILL_AFTER_ANSWERED = _session_design.REFILL_AFTER_ANSWERED

# Warm-pool fill target matches Budget speculative seed (pipe only, not N_page).
INITIAL_BATCH_SIZE = SPECULATIVE_PAGE_N
# First job writes ONE question so the learner can start immediately. Critic +
# verify run per draft after the shared draft call — a batch of 5 delays "go"
# by four quality-gate round-trips. Warm pool fills right after via remainder /
# refill jobs (capped at REFILL_BATCH_SIZE).
FIRST_QUESTION_BATCH_SIZE = 1
# Pipeline chunk only (docs/QUESTION_BUDGET_ENGINE.md). Product cook target is
# N_page from plan_page_budget / page_coverage.question_budget — never treat
# REFILL_BATCH_SIZE as the page budget.
REFILL_BATCH_SIZE = 5
# Hard ceiling on any single generate.questions job. Callers sometimes pass
# `remaining` (= full page budget); without this a large N_page becomes one
# multi-hour job instead of small rolling batches.
MAX_GENERATE_BATCH_SIZE = REFILL_BATCH_SIZE
# A generate job left in 'running' after a worker crash blocks recovery until reclaimed.
# Must sit well above p99 generation wall-clock — reclaiming a live job duplicates LLM
# work and races assertion writes. Workers do not heartbeat locked_at during the handler.
GENERATE_JOB_STALE_SECONDS = int(os.getenv("ZIVO_GENERATE_JOB_STALE_SECONDS", "1800"))

# Per-learner fields for shared newspaper editions (stored in document_learner_state).
_LEARNER_PROGRESS_KEYS = frozenset(
    {
        "answered_ids",
        "answered_on_page",
        "generated_on_page",
        "last_confirmed_answer",
        "session_items_answered",
        "selection_policy",
        "focus_concept",
        "mastery_stop",
        "revisit_due_hours",
        "concept_revisit_hours",
        "learner_ability",
        "learner_ability_se",
        "revisit_ease",
        "revisit_repetitions",
        "concept_revisit_ease",
        "concept_revisit_repetitions",
        "concept_ability",
        "concept_ability_n",
        "empty_page_streak",
        "budget_serve_mode",
    }
)


def learner_key_for(user: Any | None, guest_id: str | None) -> str | None:
    user_id = getattr(user, "id", None) if user is not None else None
    return learner_key_for_user(user_id, guest_id)


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
        "empty_page_streak": 0,
        "prompt_reselect_pages": False,
        "prompt_reselect_reason": None,
    }


def _apply_progress_defaults(progress: dict[str, Any], doc: Document) -> dict[str, Any]:
    progress.setdefault("current_page", default_progress(doc)["current_page"])
    progress.setdefault("answered_ids", [])
    progress.setdefault("answered_on_page", 0)
    progress.setdefault("generated_on_page", 0)
    progress.setdefault("generation_pending", False)
    progress.setdefault("page_coverage", {})
    progress.setdefault("transition_prep_done", {})
    progress.setdefault("empty_page_streak", 0)
    progress.setdefault("prompt_reselect_pages", False)
    progress.setdefault("prompt_reselect_reason", None)
    return progress


def _load_shared_progress(doc: Document) -> dict[str, Any]:
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
    return _apply_progress_defaults(dict(progress), doc)


def get_progress(doc: Document, *, learner_key: str | None = None) -> dict[str, Any]:
    from app.services.newspaper import is_newspaper_document

    progress = _load_shared_progress(doc)
    if not is_newspaper_document(doc):
        return progress

    session = Session.object_session(doc)
    if learner_key and session is not None:
        overlay = load_learner_progress(session, doc.id, learner_key) or {}
        for key in _LEARNER_PROGRESS_KEYS:
            if key in overlay:
                progress[key] = overlay[key]
    else:
        progress["answered_ids"] = []
        progress["answered_on_page"] = 0
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


def save_progress(
    db: Session,
    doc: Document,
    progress: dict[str, Any],
    *,
    learner_key: str | None = None,
) -> None:
    from app.services.newspaper import is_newspaper_document

    db.flush()
    db.refresh(doc, with_for_update=True)
    newspaper = is_newspaper_document(doc)
    if newspaper and learner_key:
        learner_patch = {k: v for k, v in progress.items() if k in _LEARNER_PROGRESS_KEYS}
        shared_patch = {k: v for k, v in progress.items() if k not in _LEARNER_PROGRESS_KEYS}
        if shared_patch:
            merged_shared = _merge_progress(get_progress(doc), shared_patch)
            save_progress_row(db, doc.id, merged_shared)
        if learner_patch:
            merged_learner = _merge_progress(
                get_progress(doc, learner_key=learner_key), learner_patch
            )
            learner_only = {
                k: merged_learner[k] for k in _LEARNER_PROGRESS_KEYS if k in merged_learner
            }
            save_learner_progress_row(db, doc.id, learner_key, learner_only)
        return

    merged = _merge_progress(get_progress(doc, learner_key=learner_key), progress)
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


def get_study_mode(doc: Document, *, learner_key: str | None = None) -> str:
    """The learner's per-document choice: 'adaptive' (adaptive_v1) or 'classic'
    (sequence). Falls back to the global default policy when unset."""
    from app.config import get_settings

    policy = (
        str(get_progress(doc, learner_key=learner_key).get("selection_policy") or "").strip().lower()
        or (get_settings().selection_policy or "sequence").lower()
    )
    return "classic" if policy == "sequence" else "adaptive"


def set_study_mode(
    db: Session, doc: Document, mode: str, *, learner_key: str | None = None
) -> str:
    """Persist the learner's Adaptive/Classic choice for this document and return it.
    Both policies run over the same question pool, so no regeneration is needed."""
    policy = "sequence" if str(mode).strip().lower() == "classic" else "adaptive_v1"
    save_progress(db, doc, {"selection_policy": policy}, learner_key=learner_key)
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
    programmable: bool = False,
    budget_confidence: str | None = None,
    budget_mode: str | None = None,
    budget_version: str | None = None,
    n_cov: float | None = None,
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
        # Triage's verdict on whether this page is about programming/algorithms —
        # the gate the coding-bank ETA job reads to decide whether to spawn.
        "programmable": bool(programmable),
    }
    if aspect_dedup:
        entry["aspect_dedup"] = aspect_dedup
    if budget_confidence is not None:
        entry["budget_confidence"] = budget_confidence
    if budget_mode is not None:
        entry["budget_mode"] = budget_mode
    if budget_version is not None:
        entry["budget_version"] = budget_version
    if n_cov is not None:
        entry["n_cov"] = n_cov
    save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})
    db.commit()
    db.refresh(doc)


def is_non_content_page(doc: Document, page: int) -> bool:
    """True when triage deliberately judged this page to have zero testable content."""
    return bool(get_page_coverage(doc, page).get("non_content"))


def should_skip_empty_page_vision(doc: Document) -> bool:
    """Once we already asked the learner to reselect, stop burning vision calls."""
    return bool(get_progress(doc).get("prompt_reselect_pages"))


def note_empty_page_triage(
    db: Session,
    document_id: uuid.UUID,
    *,
    vision_usable: bool,
) -> dict[str, Any]:
    """Bump blank streak / set reselect prompt after an empty-text page triage."""
    from app.services.content_worthiness import plan_empty_page_reselect

    doc = db.get(Document, document_id)
    if not doc:
        return {}
    progress = get_progress(doc)
    verdict = plan_empty_page_reselect(
        prior_streak=int(progress.get("empty_page_streak") or 0),
        vision_usable=vision_usable,
        already_prompted=bool(progress.get("prompt_reselect_pages")),
        prior_reason=progress.get("prompt_reselect_reason"),
    )
    patch: dict[str, Any] = {"empty_page_streak": verdict.streak}
    if verdict.prompt_reselect:
        patch["prompt_reselect_pages"] = True
        patch["prompt_reselect_reason"] = verdict.reason
    save_progress(db, doc, patch)
    return patch


def reset_empty_page_streak(db: Session, document_id: uuid.UUID) -> None:
    """A page with real extractable text resets the blank streak."""
    doc = db.get(Document, document_id)
    if not doc:
        return
    if int(get_progress(doc).get("empty_page_streak") or 0) == 0:
        return
    save_progress(db, doc, {"empty_page_streak": 0})


def get_question_budget(doc: Document, page: int, *, mode: Mode | None = None) -> int:
    cov = get_page_coverage(doc, page)
    # A deliberate zero verdict is honoured exactly (no floor) — the whole point
    # of open-world: ask nothing on a cover page rather than force filler.
    if cov.get("non_content"):
        return 0
    serve_mode = mode if mode is not None else serve_budget_mode(doc)
    # When serve mode differs from the cook plan (typically Test after Learn cook),
    # re-plan from stored aspects so Y and refill target follow the multiplier.
    persisted_mode = parse_budget_mode(cov.get("budget_mode") if isinstance(cov.get("budget_mode"), str) else None)
    aspects = cov.get("aspects") if isinstance(cov.get("aspects"), list) else None
    if aspects and serve_mode != persisted_mode:
        units = units_from_aspect_dicts(aspects)
        conf_raw = cov.get("budget_confidence")
        conf: Literal["high", "medium", "low"] | None = None
        if conf_raw in ("high", "medium", "low"):
            conf = conf_raw
        plan = plan_page_budget(units, mode=serve_mode, confidence=conf)
        return plan.n_page
    # Page cook target N_page from plan_page_budget (persisted at triage). A
    # missing budget means triage hasn't landed yet — seed speculative N_page
    # (confidence=low) so generation can start; a triaged 0 is honoured.
    raw = cov.get("question_budget")
    if raw is None:
        return speculative_page_budget(mode=serve_mode).n_page
    budget = int(raw)
    return max(0, budget)


def page_budgets_for_document(doc: Document, *, mode: Mode | None = None) -> list[int]:
    """N_page values for selected cookable pages (missing triage → skip)."""
    serve_mode = mode if mode is not None else serve_budget_mode(doc)
    pages = selected_page_list(doc)
    progress = get_progress(doc)
    coverage = progress.get("page_coverage") or {}
    out: list[int] = []
    for page in pages:
        entry = coverage.get(_page_key(page))
        if not isinstance(entry, dict):
            continue
        if entry.get("non_content"):
            out.append(0)
            continue
        # Prefer mode-aware planner when aspects exist; else persisted cook N.
        aspects = entry.get("aspects") if isinstance(entry.get("aspects"), list) else None
        persisted_mode = parse_budget_mode(
            entry.get("budget_mode") if isinstance(entry.get("budget_mode"), str) else None
        )
        if aspects and serve_mode != persisted_mode:
            units = units_from_aspect_dicts(aspects)
            out.append(plan_page_budget(units, mode=serve_mode).n_page)
            continue
        raw = entry.get("question_budget")
        if raw is None:
            continue
        out.append(max(0, int(raw)))
    return out


def serve_budget_mode(doc: Document, *, learner_key: str | None = None) -> Mode:
    """Active Learn/Test budget mode for serve + refill (persisted on progress)."""
    progress = get_progress(doc, learner_key=learner_key)
    return parse_budget_mode(
        progress.get("budget_serve_mode") if isinstance(progress.get("budget_serve_mode"), str) else None
    )


def set_serve_budget_mode(
    db: Session, doc: Document, mode: Mode, *, learner_key: str | None = None
) -> Mode:
    """Remember serve mode so grade/refill paths match learn-queue ?mode=."""
    resolved = parse_budget_mode(mode)
    if serve_budget_mode(doc, learner_key=learner_key) == resolved:
        return resolved
    save_progress(db, doc, {"budget_serve_mode": resolved}, learner_key=learner_key)
    db.commit()
    db.refresh(doc)
    return resolved


def effective_question_budget(
    doc: Document,
    page: int,
    progress: dict[str, Any] | None = None,
    *,
    mode: Mode | None = None,
) -> int:
    """Page generation target = full plan budget (no generate-ahead pacing)."""
    del progress  # kept for call-site compatibility
    return get_question_budget(doc, page, mode=mode)


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
    from app.services.kc_coverage import is_page_covered

    return is_page_covered(cov)


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


def edition_assertion_ids(db: Session, document_id: uuid.UUID, doc: Document) -> list[str]:
    """All active MCQs across the study range — newspaper serves the full cooked pool."""
    ids: list[str] = []
    for page in selected_page_list(doc):
        ids.extend(page_assertion_ids(db, document_id, page))
    return ids


def assertion_page_number(db: Session, assertion_id: str) -> int | None:
    row = db.execute(
        text(
            """
            SELECT (payload->>'page_number')::int AS page
            FROM intel.assertion WHERE id::text = :id
            """
        ),
        {"id": assertion_id},
    ).scalar()
    return int(row) if row is not None else None


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
    keys, _labels = _concept_signals_for_ids(db, ids)
    return keys


def _concept_signals_for_ids(
    db: Session, ids: list[str]
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """Map assertion id -> (primary_concept_key, primary_concept label)."""
    if not ids:
        return {}, {}
    rows = db.execute(
        text(
            """
            SELECT id::text AS id,
                   payload->>'primary_concept_key' AS key,
                   payload->>'primary_concept' AS label
            FROM intel.assertion
            WHERE id::text = ANY(:ids)
            """
        ),
        {"ids": ids},
    ).mappings().all()
    keys = {r["id"]: r["key"] for r in rows}
    labels = {r["id"]: r["label"] for r in rows}
    return keys, labels


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


def _exposure_for_ids(db: Session, ids: list[str]) -> dict[str, int]:
    """Map assertion id -> first-try answer count (soft novelty prior for selection).

    Missing / unseeded measurement vocabulary → empty map (novelty=1.0 for all).
    Never hard-filters candidates; Adaptive Selection only soft-weights.
    """
    if not ids:
        return {}
    try:
        from app.repositories.intel import concept_id
        from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI

        metric = concept_id(db, ANSWER_CORRECT_METRIC_URI)
    except ValueError:
        return {}
    rows = db.execute(
        text(
            """
            SELECT source_assertion_id::text AS id, COUNT(*)::int AS n
            FROM intel.measurement
            WHERE metric_concept_id = :metric
              AND source_assertion_id::text = ANY(:ids)
            GROUP BY source_assertion_id
            """
        ),
        {"metric": metric, "ids": ids},
    ).mappings().all()
    return {r["id"]: int(r["n"]) for r in rows}

def select_next_assertion(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    progress: dict[str, Any],
    *,
    page_ids: list[str] | None = None,
) -> str | None:
    """The loop's choosing step — Adaptive Selection Engine over unanswered candidates.

    See docs/ADAPTIVE_SELECTION_ENGINE.md. Classic (``sequence``) returns the first
    unanswered id. Adaptive policies score the bank; all degrade safely to sequence.
    Orchestration loads signals; focus / mastery / spaced live in ``select_next``.
    """
    page = int(progress.get("current_page") or 1)
    answered = {str(x) for x in progress.get("answered_ids") or []}
    rows = page_ids if page_ids is not None else page_assertion_ids(db, document_id, page)
    candidates = [rid for rid in rows if rid not in answered]
    if not candidates:
        return None

    from app.config import get_settings
    from app.services.adaptive_selection import (
        build_learner_state,
        normalize_policy,
        select_next,
    )

    # Per-document override (the learner's Adaptive/Classic choice) wins; otherwise
    # fall back to the global default policy.
    policy = normalize_policy(
        str(progress.get("selection_policy") or "").strip().lower()
        or (get_settings().selection_policy or "sequence")
    )
    serve_mode = "test" if str(progress.get("serve_mode") or "").lower() == "test" else "learn"
    state = build_learner_state(progress)

    difficulty_by_id: dict[str, float] = {}
    lineage_by_id: dict[str, str] = {}
    concept_by_id: dict[str, str | None] = {}
    concept_label_by_id: dict[str, str | None] = {}
    exposure_by_id: dict[str, int] = {}

    # Always load concept key/label when serve hygiene or adaptive scoring may need them.
    needs_concepts = (
        bool(state.focus_concept)
        or state.mastery_stop
        or bool(state.concept_revisit_hours)
        or policy in ("adaptive_v1", "difficulty_edge", "concept_reinforce")
    )
    if needs_concepts:
        concept_by_id, concept_label_by_id = _concept_signals_for_ids(db, candidates)
    if policy in ("adaptive_v1", "difficulty_edge"):
        difficulty_by_id = _difficulty_for_ids(db, candidates)
        if state.last_assertion_id:
            lineage_by_id = _lineage_successors(db, state.last_assertion_id, candidates)
        if policy == "adaptive_v1":
            exposure_by_id = _exposure_for_ids(db, candidates)

    verdict = select_next(
        candidates,
        state,
        policy=policy,
        mode=serve_mode,  # type: ignore[arg-type]
        concept_by_id=concept_by_id,
        concept_label_by_id=concept_label_by_id,
        difficulty_by_id=difficulty_by_id,
        lineage_by_id=lineage_by_id,
        exposure_by_id=exposure_by_id,
    )
    return verdict.assertion_id


def _candidates_matching_concept_label(
    db: Session, candidate_ids: list[str], focus: str
) -> list[str]:
    """Keep candidates whose primary_concept / key matches the Progress focus label."""
    if not candidate_ids or not focus:
        return []
    keys, labels = _concept_signals_for_ids(db, candidate_ids)
    from app.services.adaptive_selection import concept_text_matches

    needle = focus.strip()
    return [
        cid
        for cid in candidate_ids
        if concept_text_matches(needle, key=keys.get(cid), label=labels.get(cid))
    ]


def set_focus_concept(
    db: Session, doc: Document, concept: str | None, *, learner_key: str | None = None
) -> None:
    """Persist Progress → Learn concept focus (empty clears)."""
    save_progress(db, doc, {"focus_concept": (concept or "").strip() or None}, learner_key=learner_key)


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
    page = int(progress.get("current_page") or 1)
    if is_non_content_page(doc, page):
        return True
    # Unanswered questions on this page → not complete. Never let a pending flag
    # strand the learner when the pool still has cards to show.
    if next_assertion_id(db, doc.id, progress, page_ids=page_ids):
        return False
    budget = get_question_budget(doc, page)
    generated = len(page_ids) if page_ids is not None else count_assertions_on_page(db, doc.id, page)
    if generated == 0:
        return False
    coverage_done = is_coverage_complete(doc, page)
    # generation_pending is doc-level and can be set by next-page prefetch. Only
    # treat it as "still cooking THIS page" when we still have room under the
    # page plan and coverage is not done — otherwise advance.
    if (
        progress.get("generation_pending")
        and not coverage_done
        and generated < budget
    ):
        return False
    if coverage_done:
        return True
    return generated >= budget


def build_learn_queue_state(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    progress: dict[str, Any],
    *,
    mode: Mode | None = None,
    learner_key: str | None = None,
) -> dict[str, Any]:
    from app.services.newspaper import is_newspaper_document
    from app.services.session_design import SESSION_SOFT_DEFAULT, plan_session

    serve_mode = mode if mode is not None else serve_budget_mode(doc, learner_key=learner_key)
    newspaper = is_newspaper_document(doc)
    page = int(progress.get("current_page") or 1)
    study_pages = selected_page_list(doc)
    page_from, page_to = page_range_bounds(doc)
    answered_ids = [str(x) for x in progress.get("answered_ids") or []]
    answered_set = set(answered_ids)
    if newspaper:
        page_ids = edition_assertion_ids(db, document_id, doc)
    else:
        page_ids = page_assertion_ids(db, document_id, page)
    questions_generated = len(page_ids)
    questions_answered = sum(1 for row_id in page_ids if row_id in answered_set)
    cov = get_page_coverage(doc, page)
    if newspaper:
        plan_budget = max(questions_generated, 1)
    else:
        plan_budget = get_question_budget(doc, page, mode=serve_mode)
    # FE compat: generation_cap used to be a generate-ahead pace; now equals plan.
    generation_cap = plan_budget
    doc_plan = plan_document_budget(page_budgets_for_document(doc, mode=serve_mode), mode=serve_mode)
    session = plan_session(
        max(0, int(doc_plan.n_doc) - len(answered_set)),
        soft_cap=SESSION_SOFT_DEFAULT,
        mode=serve_mode,
    )
    session_items = int(progress.get("session_items_answered") or 0)
    session_break = bool(session.n_session > 0 and session_items >= session.n_session)
    # Loop's choosing step (policy-driven; defaults to sequence order).
    next_id = select_next_assertion(db, document_id, doc, progress, page_ids=page_ids)
    if newspaper and next_id:
        focus_page = assertion_page_number(db, next_id)
        if focus_page is not None:
            page = focus_page
    coverage_complete = is_coverage_complete(doc, page)
    if newspaper:
        page_complete = False
        last_study_page = study_pages[-1] if study_pages else page_to
        document_complete = (
            next_id is None
            and questions_generated > 0
            and not bool(progress.get("generation_pending"))
        )
    else:
        page_complete = is_page_complete(db, doc, progress, page_ids=page_ids)
        last_study_page = study_pages[-1] if study_pages else page_to
        document_complete = page_complete and page == last_study_page

    # Open-world empty state: surface a reason only when the whole study range
    # genuinely produced nothing to ask, so the UI can say so instead of spinning.
    no_questions_reason: str | None = None
    if document_complete and questions_generated == 0 and questions_answered == 0:
        if _study_range_has_no_questions(db, document_id, doc):
            no_questions_reason = "no_testable_content"
    elif newspaper and questions_generated == 0 and not bool(progress.get("generation_pending")):
        if _study_range_has_no_questions(db, document_id, doc):
            no_questions_reason = "no_testable_content"

    from app.services.rag_window import chat_rag_window, get_rag_window, is_rag_window_ready

    if newspaper:
        rag_pages = chat_rag_window(page, study_pages)
    else:
        rag_pages = get_rag_window(doc)
    rag_ready = is_rag_window_ready(db, document_id, doc)
    non_content = is_non_content_page(doc, page)
    triage_complete = bool(cov)
    if newspaper:
        triage_complete = any(get_page_coverage(doc, p) for p in study_pages) or questions_generated > 0

    return {
        "current_page": page,
        "page_from": page_from,
        "page_to": page_to,
        "current_assertion_id": next_id,
        "question_number": questions_answered + 1 if next_id else questions_answered,
        # Page cook / UI Y = plan_budget (N_page). session_soft is serve pacing only.
        "question_budget": plan_budget,
        "plan_budget": plan_budget,
        "generation_cap": generation_cap,
        "session_soft": session.soft_cap,
        "n_session": session.n_session,
        "session_break": session_break,
        "session_items_answered": session_items,
        "mastery_stop": bool(progress.get("mastery_stop")),
        "revisit_due_hours": progress.get("revisit_due_hours"),
        "concept_revisit_hours": progress.get("concept_revisit_hours"),
        "learner_ability_se": progress.get("learner_ability_se"),
        "document_budget": doc_plan.n_doc,
        "budget_confidence": cov.get("budget_confidence"),
        "budget_version": cov.get("budget_version"),
        "budget_mode": serve_mode,
        "questions_answered": questions_answered,
        "questions_generated": questions_generated,
        "generation_pending": bool(progress.get("generation_pending")),
        "page_triage_complete": triage_complete,
        "coverage_complete": coverage_complete,
        "page_complete": page_complete,
        "non_content": non_content,
        "no_questions_reason": no_questions_reason,
        "document_complete": document_complete,
        "prompt_reselect_pages": bool(progress.get("prompt_reselect_pages")),
        "prompt_reselect_reason": progress.get("prompt_reselect_reason"),
        "pool_available": sum(1 for row_id in page_ids if row_id not in answered_set),
        "generated_on_page": questions_generated,
        "answered_on_page": questions_answered,
        "max_per_page": plan_budget,
        "rag_window_pages": rag_pages,
        "rag_window_ready": rag_ready,
        "study_mode": get_study_mode(doc, learner_key=learner_key),
        "edition_pool": newspaper,
        "edition_question_total": questions_generated if newspaper else None,
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
    bump_aspect_attempts,
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
