"""Rolling per-page question pool — agent budget, prefetch batches, page advance."""

from __future__ import annotations

import os
import uuid
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.models import Document
from app.services.document_learn_state import load_progress as load_learn_state_row
from app.services.document_learn_state import save_progress_row
from app.services.presence import evaluate_presence
from app.services.document_learner_state import (
    document_uses_learner_overlay,
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
    plan_newspaper_display_budget,
    resolve_page_budget,
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
FIRST_QUESTION_BATCH_SIZE = _session_design.FIRST_QUESTION_BATCH_SIZE
REFILL_BATCH_SIZE = _session_design.REFILL_BATCH_SIZE
MAX_GENERATE_BATCH_SIZE = REFILL_BATCH_SIZE
# Warm-pool fill target matches Budget speculative seed (pipe only, not N_page).
INITIAL_BATCH_SIZE = SPECULATIVE_PAGE_N
# A generate job left in 'running' after a worker crash blocks recovery until reclaimed.
# Must sit well above p99 generation wall-clock — reclaiming a live job duplicates LLM
# work and races assertion writes. Workers do not heartbeat locked_at during the handler.
GENERATE_JOB_STALE_SECONDS = int(os.getenv("ZIVO_GENERATE_JOB_STALE_SECONDS", "1800"))

# Per-learner fields for shared documents (stored in document_learner_state).
_LEARNER_PROGRESS_KEYS = frozenset(
    {
        "current_page",
        "answered_ids",
        "learn_answered_ids",
        "test_answered_ids",
        "learn_complete",
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
        "prompt_reselect_pages",
        "prompt_reselect_reason",
        "transition_prep_done",
    }
)

_SERVE_MODE_SQL = "COALESCE(payload->>'serve_mode', 'learn')"

_ANSWERED_RULES = (
    Rule(when=(Pred("has_scoped", "truthy"),), action="scoped"),
    Rule(when=(Pred("learn_legacy", "truthy"),), action="legacy"),
    Rule(when=(), action="empty"),
)

_OVERLAY_KEY_RULES = (
    Rule(when=(Pred("in_overlay", "truthy"),), action="from_overlay"),
    Rule(when=(Pred("is_current_page", "truthy"),), action="default_page"),
    Rule(when=(Pred("in_defaults", "truthy"),), action="from_defaults"),
    Rule(when=(), action="pop"),
)

_SAVE_PROGRESS_RULES = (
    Rule(when=(Pred("overlay", "truthy"), Pred("has_key", "truthy")), action="split"),
    Rule(when=(Pred("overlay", "truthy"),), action="shared_only"),
    Rule(when=(), action="full"),
)

_MERGE_RULES = (
    Rule(when=(Pred("coverage", "truthy"), Pred("keep_existing", "truthy")), action="skip"),
    Rule(when=(Pred("coverage", "truthy"),), action="merge_coverage"),
    Rule(when=(), action="assign"),
)


def _as_str_list(values: Any) -> list[str]:
    return [str(x) for x in values]


def _budget_confidence(raw: Any) -> Literal["high", "medium", "low"] | None:
    return choose(raw in ("high", "medium", "low"), raw, None)


def _int_or_none(value: Any) -> int | None:
    return pick(value is not None, lambda: int(value), lambda: None)


def _list_or_none(value: Any) -> list[Any] | None:
    return choose(isinstance(value, list), value, None)


def _str_or_none(value: Any) -> str | None:
    return choose(isinstance(value, str), value, None)


def answered_ids_storage_key(mode: Mode) -> str:
    return choose(mode == "learn", "learn_answered_ids", "test_answered_ids")


def mode_answered_ids(progress: dict[str, Any], mode: Mode) -> list[str]:
    key = answered_ids_storage_key(mode)
    scoped = progress.get(key)
    legacy = progress.get("answered_ids")
    hit = first_match(
        _ANSWERED_RULES,
        {
            "has_scoped": isinstance(scoped, list) and bool(scoped),
            "learn_legacy": mode == "learn" and isinstance(legacy, list) and bool(legacy),
        },
    )
    return apply(
        hit.action,
        {
            "scoped": lambda: _as_str_list(scoped),
            "legacy": lambda: _as_str_list(legacy),
            "empty": lambda: [],
        },
    )


def overlay_mode_answered_ids(progress: dict[str, Any], mode: Mode) -> dict[str, Any]:
    """Newspaper: expose the active pool's answered ids as answered_ids."""
    out = dict(progress)
    out["answered_ids"] = mode_answered_ids(progress, mode)
    return out


def learner_key_for(user: Any | None, guest_id: str | None) -> str | None:
    user_id = pick(user is not None, lambda: getattr(user, "id", None), lambda: None)
    return learner_key_for_user(user_id, guest_id)


def default_progress(doc: Document) -> dict[str, Any]:
    study_pages = selected_page_list(doc)
    first_page = pick(
        bool(study_pages),
        lambda: study_pages[0],
        lambda: int((doc.meta or {}).get("selected_range", {}).get("from") or 1),
    )
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

    def try_row() -> dict[str, Any] | None:
        session = Session.object_session(doc)

        def load() -> dict[str, Any] | None:
            try:
                return load_learn_state_row(session, doc.id)
            except Exception:
                return None

        return pick(session is not None, load, lambda: None)

    row = pick(isinstance(doc, Document), try_row, lambda: None)
    from_meta = pick(isinstance(row, dict), lambda: row, lambda: meta.get("question_progress"))
    progress = pick(isinstance(from_meta, dict), lambda: from_meta, lambda: default_progress(doc))
    return _apply_progress_defaults(dict(progress), doc)


def _learner_progress_defaults() -> dict[str, Any]:
    return {
        "answered_ids": [],
        "learn_answered_ids": [],
        "test_answered_ids": [],
        "answered_on_page": 0,
        "generated_on_page": 0,
        "session_items_answered": 0,
        "empty_page_streak": 0,
    }


def _apply_overlay_key(
    progress: dict[str, Any],
    key: str,
    overlay: dict[str, Any],
    learner_defaults: dict[str, Any],
    doc: Document,
) -> None:
    hit = first_match(
        _OVERLAY_KEY_RULES,
        {
            "in_overlay": key in overlay,
            "is_current_page": key == "current_page",
            "in_defaults": key in learner_defaults,
        },
    )
    apply(
        hit.action,
        {
            "from_overlay": lambda: progress.__setitem__(key, overlay[key]),
            "default_page": lambda: progress.__setitem__(
                key, default_progress(doc)["current_page"]
            ),
            "from_defaults": lambda: progress.__setitem__(key, learner_defaults[key]),
            "pop": lambda: progress.pop(key, None),
        },
    )


def get_progress(doc: Document, *, learner_key: str | None = None) -> dict[str, Any]:
    progress = _load_shared_progress(doc)

    def overlay_path() -> dict[str, Any]:
        session = Session.object_session(doc)
        learner_defaults = _learner_progress_defaults()
        overlay = pick(
            bool(learner_key) and session is not None,
            lambda: load_learner_progress(session, doc.id, learner_key) or {},
            lambda: {},
        )
        for key in _LEARNER_PROGRESS_KEYS:
            _apply_overlay_key(progress, key, overlay, learner_defaults, doc)
        serve_mode = parse_budget_mode(_str_or_none(progress.get("budget_serve_mode")))
        return overlay_mode_answered_ids(progress, serve_mode)

    return pick(
        not document_uses_learner_overlay(doc),
        lambda: progress,
        overlay_path,
    )


def _merge_progress(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in patch.items():
        hit = first_match(
            _MERGE_RULES,
            {
                "coverage": key == "page_coverage"
                and isinstance(value, dict)
                and isinstance(merged.get(key), dict),
                "keep_existing": not value and bool(merged.get(key)),
            },
        )
        apply(
            hit.action,
            {
                "skip": lambda: None,
                "merge_coverage": lambda k=key, v=value: merged.__setitem__(
                    k, {**merged[k], **v}
                ),
                "assign": lambda k=key, v=value: merged.__setitem__(k, v),
            },
        )
    return merged


def save_progress(
    db: Session,
    doc: Document,
    progress: dict[str, Any],
    *,
    learner_key: str | None = None,
) -> None:
    db.flush()
    db.refresh(doc, with_for_update=True)
    shared_doc = document_uses_learner_overlay(doc)
    hit = first_match(
        _SAVE_PROGRESS_RULES,
        {"overlay": shared_doc, "has_key": bool(learner_key)},
    )

    def split() -> None:
        learner_patch = {
            k: v
            for k, v in filter(
                lambda kv: kv[0] in _LEARNER_PROGRESS_KEYS, progress.items()
            )
        }
        shared_patch = {
            k: v
            for k, v in filter(
                lambda kv: kv[0] not in _LEARNER_PROGRESS_KEYS, progress.items()
            )
        }

        def save_shared() -> None:
            merged_shared = _merge_progress(get_progress(doc), shared_patch)
            save_progress_row(db, doc.id, merged_shared)

        def save_learner() -> None:
            merged_learner = _merge_progress(
                get_progress(doc, learner_key=learner_key), learner_patch
            )
            learner_only = {
                k: merged_learner[k]
                for k in filter(lambda key: key in merged_learner, _LEARNER_PROGRESS_KEYS)
            }
            save_learner_progress_row(db, doc.id, learner_key, learner_only)

        pick(bool(shared_patch), save_shared, lambda: None)
        pick(bool(learner_patch), save_learner, lambda: None)

    def shared_only() -> None:
        shared_patch = {
            k: v
            for k, v in filter(
                lambda kv: kv[0] not in _LEARNER_PROGRESS_KEYS, progress.items()
            )
        }

        def persist() -> None:
            merged = _merge_progress(get_progress(doc), shared_patch)
            save_progress_row(db, doc.id, merged)

        pick(not shared_patch, lambda: None, persist)

    def full() -> None:
        merged = _merge_progress(get_progress(doc, learner_key=learner_key), progress)
        save_progress_row(db, doc.id, merged)
        user = doc.account_id

        def upsert() -> None:
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

        pick(bool(user), upsert, lambda: None)

    apply(hit.action, {"split": split, "shared_only": shared_only, "full": full})


def get_study_mode(doc: Document, *, learner_key: str | None = None) -> str:
    """The learner's per-document choice: 'adaptive' (adaptive_v1) or 'classic'
    (sequence). Falls back to the global default policy when unset."""
    from app.config import get_settings
    from app.services.adaptive_selection import label_study_mode

    policy = (
        str(get_progress(doc, learner_key=learner_key).get("selection_policy") or "").strip().lower()
        or (get_settings().selection_policy or "sequence").lower()
    )
    return label_study_mode(policy)


def set_study_mode(
    db: Session, doc: Document, mode: str, *, learner_key: str | None = None
) -> str:
    """Persist the learner's Adaptive/Classic choice for this document and return it.
    Both policies run over the same question pool, so no regeneration is needed."""
    from app.services.adaptive_selection import label_study_mode, persist_study_mode

    policy = persist_study_mode(mode)
    save_progress(db, doc, {"selection_policy": policy}, learner_key=learner_key)
    return label_study_mode(policy)


def _page_key(page: int) -> str:
    return str(page)


def get_page_coverage(doc: Document, page: int) -> dict[str, Any]:
    progress = get_progress(doc)
    coverage = progress.get("page_coverage") or {}
    entry = coverage.get(_page_key(page))
    return pick(isinstance(entry, dict), lambda: entry, lambda: {})


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
    debuggable: bool = False,
    budget_confidence: str | None = None,
    budget_mode: str | None = None,
    budget_version: str | None = None,
    n_cov: float | None = None,
    test_question_budget: int | None = None,
    test_aspects: list[dict[str, Any]] | None = None,
) -> None:
    doc = db.get(Document, document_id)
    return pick(
        evaluate_presence(doc).action == "missing",
        lambda: None,
        lambda: _save_page_coverage_entry(
            db,
            doc,
            page=page,
            question_budget=question_budget,
            aspects=aspects,
            rationale=rationale,
            triage_activity_id=triage_activity_id,
            aspect_dedup=aspect_dedup,
            content_type=content_type,
            non_content=non_content,
            programmable=programmable,
            debuggable=debuggable,
            budget_confidence=budget_confidence,
            budget_mode=budget_mode,
            budget_version=budget_version,
            n_cov=n_cov,
            test_question_budget=test_question_budget,
            test_aspects=test_aspects,
        ),
    )


def _put_if(entry: dict[str, Any], flag: bool, key: str, value: Any) -> None:
    pick(flag, lambda: entry.__setitem__(key, value), lambda: None)


def _save_page_coverage_entry(
    db: Session,
    doc: Document,
    *,
    page: int,
    question_budget: int,
    aspects: list[dict[str, Any]],
    rationale: str,
    triage_activity_id: str | None,
    aspect_dedup: dict[str, Any] | None,
    content_type: str | None,
    non_content: bool,
    programmable: bool,
    debuggable: bool,
    budget_confidence: str | None,
    budget_mode: str | None,
    budget_version: str | None,
    n_cov: float | None,
    test_question_budget: int | None,
    test_aspects: list[dict[str, Any]] | None,
) -> None:
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
        "debuggable": bool(debuggable),
    }
    _put_if(entry, bool(aspect_dedup), "aspect_dedup", aspect_dedup)
    _put_if(entry, budget_confidence is not None, "budget_confidence", budget_confidence)
    _put_if(entry, budget_mode is not None, "budget_mode", budget_mode)
    _put_if(entry, budget_version is not None, "budget_version", budget_version)
    _put_if(entry, n_cov is not None, "n_cov", n_cov)
    _put_if(entry, test_question_budget is not None, "test_question_budget", test_question_budget)
    _put_if(entry, test_aspects is not None, "test_aspects", test_aspects)
    save_progress(db, doc, {"page_coverage": {_page_key(page): entry}})
    db.commit()
    db.refresh(doc)


def is_non_content_page(doc: Document, page: int) -> bool:
    """True when triage deliberately judged this page to have zero testable content."""
    return bool(get_page_coverage(doc, page).get("non_content"))


def should_skip_empty_page_vision(doc: Document) -> bool:
    """Once we already asked the learner to reselect, stop burning vision calls."""
    return _content_worthiness.should_skip_empty_page_vision(
        already_prompted=bool(get_progress(doc).get("prompt_reselect_pages"))
    )


def note_empty_page_triage(
    db: Session,
    document_id: uuid.UUID,
    *,
    vision_usable: bool,
) -> dict[str, Any]:
    """Bump blank streak / set reselect prompt after an empty-text page triage."""
    from app.services.content_worthiness import plan_empty_page_reselect

    doc = db.get(Document, document_id)

    def bump() -> dict[str, Any]:
        progress = get_progress(doc)
        verdict = plan_empty_page_reselect(
            prior_streak=int(progress.get("empty_page_streak") or 0),
            vision_usable=vision_usable,
            already_prompted=bool(progress.get("prompt_reselect_pages")),
            prior_reason=progress.get("prompt_reselect_reason"),
        )
        patch: dict[str, Any] = {"empty_page_streak": verdict.streak}

        def mark_prompt() -> None:
            patch["prompt_reselect_pages"] = True
            patch["prompt_reselect_reason"] = verdict.reason

        pick(verdict.prompt_reselect, mark_prompt, lambda: None)
        save_progress(db, doc, patch)
        return patch

    return pick(evaluate_presence(doc).action == "missing", lambda: {}, bump)


def reset_empty_page_streak(db: Session, document_id: uuid.UUID) -> None:
    """A page with real extractable text resets the blank streak."""
    doc = db.get(Document, document_id)

    def maybe_reset() -> None:
        pick(
            int(get_progress(doc).get("empty_page_streak") or 0) == 0,
            lambda: None,
            lambda: save_progress(db, doc, {"empty_page_streak": 0}),
        )

    pick(evaluate_presence(doc).action == "missing", lambda: None, maybe_reset)


def get_test_question_budget(doc: Document, page: int) -> int:
    """Newspaper test pool cook target (m=3); 0 when non-content or unset."""
    from app.services.question_budget import resolve_test_question_budget

    cov = get_page_coverage(doc, page)
    aspects = _list_or_none(cov.get("aspects"))
    units = pick(bool(aspects), lambda: units_from_aspect_dicts(aspects), lambda: None)
    conf = _budget_confidence(cov.get("budget_confidence"))
    stored_budget = _int_or_none(cov.get("test_question_budget"))
    return resolve_test_question_budget(
        non_content=bool(cov.get("non_content")),
        stored_test_budget=stored_budget,
        units=units,
        confidence=conf,
    )


def get_question_budget(doc: Document, page: int, *, mode: Mode | None = None) -> int:
    from app.services.newspaper import is_newspaper_document

    cov = get_page_coverage(doc, page)
    serve_mode = pick(mode is not None, lambda: mode, lambda: serve_budget_mode(doc))
    newspaper = is_newspaper_document(doc)
    persisted_mode = parse_budget_mode(_str_or_none(cov.get("budget_mode")))
    aspects = _list_or_none(cov.get("aspects"))
    units = pick(bool(aspects), lambda: units_from_aspect_dicts(aspects), lambda: None)
    conf = _budget_confidence(cov.get("budget_confidence"))
    stored_budget = _int_or_none(cov.get("question_budget"))
    non_content = bool(cov.get("non_content"))
    test_n = pick(
        not non_content and newspaper and serve_mode == "test",
        lambda: get_test_question_budget(doc, page),
        lambda: 0,
    )
    n = resolve_page_budget(
        non_content=non_content,
        newspaper_test=newspaper and serve_mode == "test",
        newspaper_test_n=test_n,
        serve_mode=serve_mode,
        persisted_mode=persisted_mode,
        units=units,
        confidence=conf,
        stored_budget=stored_budget,
    )
    return choose(n is None, 0, n)


def page_budgets_for_document(doc: Document, *, mode: Mode | None = None) -> list[int]:
    """N_page values for selected cookable pages (missing triage → skip)."""
    from app.services.newspaper import is_newspaper_document

    serve_mode = pick(mode is not None, lambda: mode, lambda: serve_budget_mode(doc))
    newspaper = is_newspaper_document(doc)
    pages = selected_page_list(doc)
    progress = get_progress(doc)
    coverage = progress.get("page_coverage") or {}
    out: list[int] = []
    for page in pages:
        entry = coverage.get(_page_key(page))

        def consider(current_page: int = page, row: Any = entry) -> None:
            aspects = _list_or_none(row.get("aspects"))
            persisted_mode = parse_budget_mode(_str_or_none(row.get("budget_mode")))
            conf = _budget_confidence(row.get("budget_confidence"))
            units = pick(bool(aspects), lambda: units_from_aspect_dicts(aspects), lambda: None)
            stored_budget = _int_or_none(row.get("question_budget"))
            non_content = bool(row.get("non_content"))
            newspaper_test = newspaper and serve_mode == "test"
            test_n = pick(
                newspaper_test and not non_content,
                lambda: get_test_question_budget(doc, current_page),
                lambda: 0,
            )
            n = resolve_page_budget(
                non_content=non_content,
                newspaper_test=newspaper_test,
                newspaper_test_n=test_n,
                serve_mode=serve_mode,
                persisted_mode=persisted_mode,
                units=units,
                confidence=conf,
                stored_budget=stored_budget,
                missing="omit",
            )
            pick(n is None, lambda: None, lambda: out.append(n))

        pick(isinstance(entry, dict), consider, lambda: None)
    return out


def serve_budget_mode(doc: Document, *, learner_key: str | None = None) -> Mode:
    """Active Learn/Test budget mode for serve + refill (persisted on progress)."""
    progress = get_progress(doc, learner_key=learner_key)
    return parse_budget_mode(_str_or_none(progress.get("budget_serve_mode")))


def set_serve_budget_mode(
    db: Session, doc: Document, mode: Mode, *, learner_key: str | None = None
) -> Mode:
    """Remember serve mode so grade/refill paths match learn-queue ?mode=."""
    resolved = parse_budget_mode(mode)
    return pick(
        serve_budget_mode(doc, learner_key=learner_key) == resolved,
        lambda: resolved,
        lambda: (
            save_progress(db, doc, {"budget_serve_mode": resolved}, learner_key=learner_key),
            db.commit(),
            db.refresh(doc),
            resolved,
        )[-1],
    )


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
    from app.services.kc_coverage import evaluate_page_coverage_complete

    return evaluate_page_coverage_complete(get_page_coverage(doc, page)).complete


def count_assertions_on_page(
    db: Session,
    document_id: uuid.UUID,
    page: int,
    *,
    serve_mode: Mode | None = None,
) -> int:
    return len(page_assertion_ids(db, document_id, page, serve_mode=serve_mode))


def page_assertion_ids(
    db: Session,
    document_id: uuid.UUID,
    page: int,
    *,
    serve_mode: Mode | None = None,
) -> list[str]:
    """Ordered assertion ids for a page — facet table first, JSONB fallback."""
    from_facets = page_assertion_ids_from_facets(
        db, document_id, page, serve_mode=serve_mode
    )

    def from_jsonb() -> list[str]:
        mode_filter = pick(
            serve_mode is not None,
            lambda: f"AND {_SERVE_MODE_SQL} = :serve_mode",
            lambda: "",
        )
        params: dict[str, Any] = {"artifact_id": str(document_id), "page": page}
        pick(
            serve_mode is not None,
            lambda: params.__setitem__("serve_mode", serve_mode),
            lambda: None,
        )
        return list(
            db.execute(
                text(
                    f"""
                SELECT id::text FROM intel.assertion
                WHERE payload->>'artifact_id' = :artifact_id
                  AND status = 'active'
                  AND (payload->>'page_number')::int = :page
                  {mode_filter}
                ORDER BY (payload->>'sequence')::int ASC
                """
                ),
                params,
            )
            .scalars()
            .all()
        )

    return pick(from_facets is not None, lambda: from_facets, from_jsonb)


def edition_assertion_ids(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    *,
    serve_mode: Mode | None = None,
) -> list[str]:
    """All active MCQs across the study range — newspaper serves the full cooked pool."""
    ids: list[str] = []
    for page in selected_page_list(doc):
        ids.extend(page_assertion_ids(db, document_id, page, serve_mode=serve_mode))
    return ids


def newspaper_learn_pool_complete(
    db: Session, document_id: uuid.UUID, doc: Document, progress: dict[str, Any]
) -> bool:
    """True when every learn-pool MCQ in the edition has been answered."""
    learn_ids = edition_assertion_ids(db, document_id, doc, serve_mode="learn")
    answered = set(mode_answered_ids(progress, "learn"))
    return pick(
        not learn_ids,
        lambda: False,
        lambda: all(row_id in answered for row_id in learn_ids),
    )


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
    return pick(row is not None, lambda: int(row), lambda: None)


def count_answered_on_page(
    db: Session,
    document_id: uuid.UUID,
    page: int,
    answered_ids: list[str],
) -> int:
    return pick(
        not answered_ids,
        lambda: 0,
        lambda: int(
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
        ),
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
    rows = pick(
        page_ids is not None,
        lambda: page_ids,
        lambda: page_assertion_ids(db, document_id, page),
    )
    return sum(1 for row_id in filter(lambda rid: rid not in answered, rows))


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
    return pick(not ids, lambda: ({}, {}), lambda: _concept_signals_loaded(db, ids))


def _concept_signals_loaded(
    db: Session, ids: list[str]
) -> tuple[dict[str, str | None], dict[str, str | None]]:
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
    return pick(not ids, lambda: {}, lambda: _difficulty_loaded(db, ids))


def _difficulty_loaded(db: Session, ids: list[str]) -> dict[str, float]:
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
    return pick(not ids, lambda: {}, lambda: _exposure_loaded(db, ids))


def _exposure_loaded(db: Session, ids: list[str]) -> dict[str, int]:
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


def _load_selection_signals(
    db: Session,
    candidates: list[str],
    *,
    policy: str,
    state: Any,
) -> tuple[
    dict[str, str | None],
    dict[str, str | None],
    dict[str, float],
    dict[str, str],
    dict[str, int],
]:
    from app.services.adaptive_selection import plan_signal_load

    plan = plan_signal_load(policy=policy, state=state)
    concepts = pick(
        plan.load_concepts,
        lambda: _concept_signals_for_ids(db, candidates),
        lambda: ({}, {}),
    )
    difficulty_by_id = pick(
        plan.load_difficulty,
        lambda: _difficulty_for_ids(db, candidates),
        lambda: {},
    )
    lineage_by_id = pick(
        plan.load_lineage and bool(state.last_assertion_id),
        lambda: _lineage_successors(db, state.last_assertion_id, candidates),
        lambda: {},
    )
    exposure_by_id = pick(
        plan.load_exposure,
        lambda: _exposure_for_ids(db, candidates),
        lambda: {},
    )
    return (
        concepts[0],
        concepts[1],
        difficulty_by_id,
        lineage_by_id,
        exposure_by_id,
    )


def _unanswered_candidates(
    db: Session,
    document_id: uuid.UUID,
    progress: dict[str, Any],
    page_ids: list[str] | None,
) -> list[str]:
    page = int(progress.get("current_page") or 1)
    answered = {str(x) for x in progress.get("answered_ids") or []}
    rows = pick(
        page_ids is not None,
        lambda: page_ids,
        lambda: page_assertion_ids(db, document_id, page),
    )
    return list(filter(lambda rid: rid not in answered, rows))


def _select_verdict(
    db: Session,
    candidates: list[str],
    progress: dict[str, Any],
) -> Any:
    from app.config import get_settings
    from app.services.adaptive_selection import (
        build_learner_state,
        normalize_policy,
        select_next,
    )

    policy = normalize_policy(
        str(progress.get("selection_policy") or "").strip().lower()
        or (get_settings().selection_policy or "sequence")
    )
    serve_mode = parse_budget_mode(_str_or_none(progress.get("budget_serve_mode")))
    state = build_learner_state(progress)
    (
        concept_by_id,
        concept_label_by_id,
        difficulty_by_id,
        lineage_by_id,
        exposure_by_id,
    ) = _load_selection_signals(db, candidates, policy=policy, state=state)
    return select_next(
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
    del doc
    candidates = _unanswered_candidates(db, document_id, progress, page_ids)
    return pick(
        not candidates,
        lambda: None,
        lambda: _select_verdict(db, candidates, progress).assertion_id,
    )


def selection_reason_for(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    progress: dict[str, Any],
    *,
    page_ids: list[str] | None = None,
) -> str | None:
    """Human-readable "why this question now" for the chosen assertion.

    Mirrors select_next_assertion's scoring so the Learn UI can show the learner
    *why* this card came up (focus concept, spaced revisit, mastery reinforce,
    new page, or plain sequence order). Returns a stable, short label string.
    """
    del doc
    candidates = _unanswered_candidates(db, document_id, progress, page_ids)

    def reason() -> str | None:
        verdict = _select_verdict(db, candidates, progress)
        return pick(
            not verdict.assertion_id,
            lambda: None,
            lambda: human_selection_reason(verdict.rationale, None),
        )

    return pick(not candidates, lambda: None, reason)


def human_selection_reason(rationale: str, state: object | None = None) -> str:
    """Compat wrapper: Adaptive Selection owns the learner-facing label."""
    del state
    from app.services.adaptive_selection import label_selection_reason

    return label_selection_reason(rationale)


def _candidates_matching_concept_label(
    db: Session, candidate_ids: list[str], focus: str
) -> list[str]:
    """Keep candidates whose primary_concept / key matches the Progress focus label."""
    return pick(
        not candidate_ids or not focus,
        lambda: [],
        lambda: _filter_focus_candidates(db, candidate_ids, focus),
    )


def _filter_focus_candidates(
    db: Session, candidate_ids: list[str], focus: str
) -> list[str]:
    keys, labels = _concept_signals_for_ids(db, candidate_ids)
    from app.services.adaptive_selection import concept_text_matches

    needle = focus.strip()
    return list(
        filter(
            lambda cid: concept_text_matches(
                needle, key=keys.get(cid), label=labels.get(cid)
            ),
            candidate_ids,
        )
    )


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
    return pick(not candidate_ids, lambda: {}, lambda: _lineage_rows(db, from_assertion_id, candidate_ids))


def _lineage_rows(
    db: Session, from_assertion_id: str, candidate_ids: list[str]
) -> dict[str, str]:
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
    return pick(
        evaluate_presence(doc).action == "missing",
        lambda: None,
        lambda: select_next_assertion(db, document_id, doc, progress, page_ids=page_ids),
    )


def selected_page_list(doc: Document) -> list[int]:
    """Ordered study pages — explicit list or contiguous from/to range."""
    selected = (doc.meta or {}).get("selected_range") or {}
    pages = selected.get("pages")

    def from_list() -> list[int]:
        return sorted({int(p) for p in filter(lambda p: int(p) >= 1, pages)})

    def from_range() -> list[int]:
        lo = int(selected.get("from") or 1)
        hi = int(selected.get("to") or lo)
        return list(range(lo, hi + 1))

    return pick(isinstance(pages, list) and bool(pages), from_list, from_range)


def page_range_bounds(doc: Document) -> tuple[int, int]:
    pages = selected_page_list(doc)
    return pick(not pages, lambda: (1, 1), lambda: (pages[0], pages[-1]))


def is_page_complete(
    db: Session,
    doc: Document,
    progress: dict[str, Any],
    *,
    page_ids: list[str] | None = None,
) -> bool:
    from app.services.session_design import evaluate_page_complete

    page = int(progress.get("current_page") or 1)
    non_content = is_non_content_page(doc, page)

    def content_signals() -> tuple[bool, bool, bool, int, int, bool]:
        has_next = bool(next_assertion_id(db, doc.id, progress, page_ids=page_ids))
        rows = pick(
            page_ids is not None,
            lambda: page_ids,
            lambda: page_assertion_ids(db, doc.id, page),
        )
        answered = {str(x) for x in progress.get("answered_ids") or []}
        all_served = bool(rows) and all(rid in answered for rid in rows)

        def active_job() -> bool:
            from app.services import question_pool_jobs

            return bool(question_pool_jobs._has_active_generate_job_for_page(db, doc.id, page))

        has_active = pick(all_served, active_job, lambda: False)
        budget = get_question_budget(doc, page)
        generated = pick(
            page_ids is not None,
            lambda: len(page_ids),
            lambda: count_assertions_on_page(db, doc.id, page),
        )
        coverage_done = is_coverage_complete(doc, page)
        return has_next, all_served, has_active, generated, budget, coverage_done

    has_next, all_served, has_active, generated, budget, coverage_done = pick(
        not non_content,
        content_signals,
        lambda: (False, False, False, 0, 0, False),
    )
    verdict = evaluate_page_complete(
        non_content=non_content,
        has_next_card=has_next,
        all_served_answered=all_served,
        has_active_generate=has_active,
        generated=generated,
        budget=budget,
        coverage_done=coverage_done,
        generation_pending=bool(progress.get("generation_pending")),
    )
    return verdict.complete


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
    from app.services.session_design import (
        SESSION_SOFT_DEFAULT,
        evaluate_document_complete,
        evaluate_newspaper_learn_complete,
        evaluate_session_break,
        plan_newspaper_serve_scope,
        plan_session,
    )

    serve_mode = pick(
        mode is not None,
        lambda: mode,
        lambda: serve_budget_mode(doc, learner_key=learner_key),
    )
    newspaper = is_newspaper_document(doc)
    page = int(progress.get("current_page") or 1)
    study_pages = selected_page_list(doc)
    page_from, page_to = page_range_bounds(doc)
    answered_ids = [str(x) for x in progress.get("answered_ids") or []]
    answered_set = set(answered_ids)
    pool_mode: Mode | None = choose(newspaper, serve_mode, None)
    scope = plan_newspaper_serve_scope(newspaper=newspaper)

    def edition_ids() -> tuple[list[str], list[str], list[str], list[str]]:
        return (
            edition_assertion_ids(db, document_id, doc, serve_mode=pool_mode),
            edition_assertion_ids(db, document_id, doc, serve_mode="learn"),
            edition_assertion_ids(db, document_id, doc, serve_mode="test"),
            page_assertion_ids(db, document_id, page, serve_mode=pool_mode),
        )

    def page_only() -> tuple[list[str], list[str], list[str], list[str]]:
        ids = page_assertion_ids(db, document_id, page)
        return ids, [], [], ids

    page_ids, learn_pool_ids, test_pool_ids, selection_ids = pick(
        scope.stats_from_edition, edition_ids, page_only
    )
    questions_generated = len(page_ids)
    questions_answered = sum(
        1 for row_id in filter(lambda rid: rid in answered_set, page_ids)
    )
    cov = get_page_coverage(doc, page)

    def edition_budget_path() -> tuple[int, bool, bool]:
        plan_budget = plan_newspaper_display_budget(generated=questions_generated)
        learn_answered_set = set(mode_answered_ids(progress, "learn"))
        learn_complete = evaluate_newspaper_learn_complete(
            learn_pool_count=len(learn_pool_ids),
            all_learn_answered=all(
                row_id in learn_answered_set for row_id in learn_pool_ids
            ),
            generation_pending=bool(progress.get("generation_pending")),
        )
        return plan_budget, learn_complete, len(test_pool_ids) > 0

    plan_budget, learn_complete, test_pool_ready = pick(
        scope.edition_budget,
        edition_budget_path,
        lambda: (get_question_budget(doc, page, mode=serve_mode), False, False),
    )
    # FE compat: generation_cap used to be a generate-ahead pace; now equals plan.
    generation_cap = plan_budget
    doc_plan = plan_document_budget(page_budgets_for_document(doc, mode=serve_mode), mode=serve_mode)
    session = plan_session(
        max(0, int(doc_plan.n_doc) - len(answered_set)),
        soft_cap=SESSION_SOFT_DEFAULT,
        mode=serve_mode,
    )
    session_items = int(progress.get("session_items_answered") or 0)
    session_break = evaluate_session_break(
        session_items=session_items, n_session=session.n_session
    ).should_break
    # Loop's choosing step (policy-driven; defaults to sequence order).
    next_id = select_next_assertion(db, document_id, doc, progress, page_ids=selection_ids)
    selection_reason = pick(
        bool(next_id),
        lambda: selection_reason_for(
            db, document_id, doc, progress, page_ids=selection_ids
        ),
        lambda: None,
    )

    def edition_stats() -> tuple[int, int, int]:
        total = len(selection_ids)
        answered = sum(
            1 for row_id in filter(lambda rid: rid in answered_set, selection_ids)
        )
        number = pick(
            bool(next_id) and total > 0,
            lambda: answered + 1,
            lambda: answered,
        )
        return total, answered, number

    edition_page_question_total, edition_page_questions_answered, current_page_question_number = pick(
        scope.stats_from_edition,
        edition_stats,
        lambda: (None, None, None),
    )
    coverage_complete = is_coverage_complete(doc, page)
    last_study_page = pick(bool(study_pages), lambda: study_pages[-1], lambda: page_to)
    page_complete = is_page_complete(db, doc, progress, page_ids=selection_ids)
    document_complete = evaluate_document_complete(
        page_complete=page_complete,
        on_last_page=page == last_study_page,
        newspaper=newspaper,
        generation_pending=bool(progress.get("generation_pending")),
    )

    from app.services.content_worthiness import evaluate_empty_study_reason, should_probe_empty_study_range

    maybe_empty = should_probe_empty_study_range(
        document_complete=document_complete,
        newspaper=newspaper,
        questions_generated=questions_generated,
        questions_answered=questions_answered,
        generation_pending=bool(progress.get("generation_pending")),
    )
    no_questions_reason = evaluate_empty_study_reason(
        document_complete=document_complete,
        newspaper=newspaper,
        questions_generated=questions_generated,
        questions_answered=questions_answered,
        generation_pending=bool(progress.get("generation_pending")),
        range_has_no_questions=pick(
            maybe_empty,
            lambda: _study_range_has_no_questions(db, document_id, doc),
            lambda: False,
        ),
    )

    from app.services.rag_window import get_rag_window, is_rag_window_ready
    from app.services.tutor_retrieval import plan_queue_rag_pages

    rag_pages = list(
        plan_queue_rag_pages(
            newspaper=newspaper,
            current_page=page,
            study_pages=study_pages,
            stored_window=pick(newspaper, lambda: None, lambda: get_rag_window(doc)),
        )
    )
    rag_ready = is_rag_window_ready(db, document_id, doc)
    non_content = is_non_content_page(doc, page)

    def newspaper_triage() -> bool:
        from app.services.session_design import evaluate_newspaper_triage_complete

        return evaluate_newspaper_triage_complete(
            has_any_coverage=any(get_page_coverage(doc, p) for p in study_pages),
            questions_generated=questions_generated,
        )

    triage_complete = pick(newspaper, newspaper_triage, lambda: bool(cov))

    def load_lesson() -> Any:
        from app.services.page_lessons import get_lesson

        return get_lesson(db, document_id, page)

    page_lesson = pick(
        serve_mode == "learn" and not non_content, load_lesson, lambda: None
    )

    return {
        "current_page": page,
        "page_from": page_from,
        "page_to": page_to,
        "current_assertion_id": next_id,
        "selection_reason": selection_reason,
        "question_number": pick(
            bool(next_id), lambda: questions_answered + 1, lambda: questions_answered
        ),
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
        "learn_complete": choose(newspaper, learn_complete, None),
        "test_pool_ready": choose(newspaper, test_pool_ready, None),
        "page_lesson": page_lesson,
        "prompt_reselect_pages": bool(progress.get("prompt_reselect_pages")),
        "prompt_reselect_reason": progress.get("prompt_reselect_reason"),
        "pool_available": sum(
            1 for row_id in filter(lambda rid: rid not in answered_set, selection_ids)
        ),
        "generated_on_page": questions_generated,
        "answered_on_page": questions_answered,
        "max_per_page": plan_budget,
        "rag_window_pages": rag_pages,
        "rag_window_ready": rag_ready,
        "study_mode": get_study_mode(doc, learner_key=learner_key),
        "edition_pool": newspaper,
        "edition_question_total": choose(newspaper, questions_generated, None),
        "edition_page_question_total": edition_page_question_total,
        "edition_page_questions_answered": edition_page_questions_answered,
        "current_page_question_number": current_page_question_number,
    }


def _study_range_has_no_questions(
    db: Session, document_id: uuid.UUID, doc: Document
) -> bool:
    """True when not a single active question exists across the selected pages."""
    pages = selected_page_list(doc)

    def count_range() -> bool:
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

    return pick(not pages, lambda: False, count_range)


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
