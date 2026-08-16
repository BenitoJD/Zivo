"""Session Design Engine - soft serve-session length + pool schedule thresholds.

Design: docs/SESSION_DESIGN_ENGINE.md
Version: qb.session.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping, Sequence

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.question_budget import SESSION_SOFT
from app.services.token_budget import SUMMARIZE_SINGLE_SHOT_MAX_TOKENS

SESSION_VERSION = "qb.session.v1"
SESSION_SOFT_DEFAULT = SESSION_SOFT

# Serve-pipeline schedule (ops thresholds owned here; orchestration reads only).
# Env overrides keep load-test knobs without scattering policy in question_pool.
import os as _os

READY_LOW_WATER = int(_os.getenv("ZIVO_READY_LOW_WATER", "8"))
TRANSITION_PREFETCH_RATIO = float(_os.getenv("ZIVO_TRANSITION_PREFETCH_RATIO", "0.45"))
TRANSITION_GENERATION_RATIO = float(_os.getenv("ZIVO_TRANSITION_GENERATION_RATIO", "0.15"))
EAGER_TRIAGE_LOOKAHEAD = int(_os.getenv("ZIVO_EAGER_TRIAGE_LOOKAHEAD", "5"))
REFILL_AFTER_ANSWERED = int(_os.getenv("ZIVO_REFILL_AFTER_ANSWERED", "2"))

# Pipeline chunk only (not N_page). First job writes one question so Learn unblocks.
FIRST_QUESTION_BATCH_SIZE = 1
REFILL_BATCH_SIZE = 5
# Cap background-prep so one huge PDF cannot flood the ETA queue.
BACKGROUND_PREP_MAX_QUESTIONS = 200
# Newspaper catalog heal: indexing vs ready-doc-uncooked timeouts (minutes).
STUCK_INDEXING_TIMEOUT_MINUTES = 30
STUCK_READY_DOC_TIMEOUT_MINUTES = 20
# Ingest ETA recovery: grace before re-enqueue, plus per-tick batch caps.
INDEXING_RECOVERY_GRACE_MINUTES = 2
PREPPING_RECOVERY_GRACE_MINUTES = 5
INDEXING_RECOVERY_BATCH = 50
PREPPING_RECOVERY_BATCH = 25
# Learners only see / keep editions inside this rolling window.
NEWSPAPER_RETENTION_DAYS = 30
# Auxiliary study artifacts (flashcards, outline, palace, quiz, bundle).
FLASHCARD_MAX = 24
TOPIC_OUTLINE_MAX = 15
MEMORY_PALACE_MIN_STATIONS = 4
MEMORY_PALACE_MAX_STATIONS = 8
QUIZ_MAX_QUESTIONS = 40
QUIZ_DEFAULT_COUNT = 10
BUNDLE_UPLOAD_MAX_FILES = 20
BUNDLE_UPLOAD_MIN_FILES = 2
OPTION_COACH_PAGE_LIMIT = 60
GRADE_FEEDBACK_TIMEOUT_SECONDS = int(_os.getenv("ZIVO_GRADE_FEEDBACK_TIMEOUT", "12"))
INTERVIEW_MCQ_MAX_OPTIONS = 6
INTERVIEW_CODING_MAX_TESTS = 6
INTERVIEW_ASKED_CONTEXT = 8
SAVED_NOTES_MAX = 500
BRAINSTORM_IDEAS_MAX = 500
BRAINSTORM_TREE_DEPTH_MAX = 12

# Interview Mode round plans — session schedule for mock interviews.
# Each round: name, kind ("mcq" | "typed" | "coding"), focus, questions (count).
InterviewRound = dict[str, Any]

INTERVIEW_ROUND_PLANS: dict[str, list[InterviewRound]] = {
    "service": [
        {
            "name": "Aptitude & Coding",
            "kind": "mcq",
            "questions": 3,
            "focus": (
                "quantitative aptitude, logical reasoning, and basic coding / "
                "output-prediction MCQs"
            ),
        },
        {
            "name": "Technical",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "core CS fundamentals and project / tech-stack questions from the "
                "resume (OOP, DBMS, SQL, the candidate's listed technologies)"
            ),
        },
        {
            "name": "HR & Behavioural",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "behavioural / HR questions (strengths, weaknesses, motivation, "
                "teamwork) — expect STAR-style answers"
            ),
        },
    ],
    "product": [
        {
            "name": "DSA / Coding",
            "kind": "coding",
            "questions": 1,
            "focus": (
                "a self-contained data-structures & algorithms problem solved by "
                "reading stdin and printing to stdout (LeetCode-easy/medium)"
            ),
        },
        {
            "name": "Technical Fundamentals",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "deep CS fundamentals relevant to the resume (operating systems, "
                "networks, databases, language internals)"
            ),
        },
        {
            "name": "System Design (HLD)",
            "kind": "typed",
            "questions": 1,
            "focus": (
                "high-level system design: functional & non-functional requirements, "
                "capacity estimation, architecture, scaling, and trade-offs "
                "(CAP, consistency, caching)"
            ),
        },
        {
            "name": "Low-Level Design (LLD)",
            "kind": "typed",
            "questions": 1,
            "focus": (
                "object-oriented / low-level design: class modelling, SOLID, "
                "design patterns, concurrency, and schema"
            ),
        },
        {
            "name": "Behavioural",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "behavioural / culture-fit questions grounded in the candidate's "
                "projects and experience — STAR answers"
            ),
        },
    ],
    "bank": [
        {
            "name": "Aptitude",
            "kind": "mcq",
            "questions": 3,
            "focus": "quantitative aptitude, logical reasoning, and basic technical MCQs",
        },
        {
            "name": "Technical",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "core CS fundamentals and the candidate's tech stack "
                "(OOP, DBMS, SQL, data structures)"
            ),
        },
        {
            "name": "Domain & Systems",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "banking / fintech domain awareness, secure & reliable system design, "
                "transactions and consistency"
            ),
        },
        {
            "name": "HR",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "behavioural / HR questions — stability, integrity, teamwork, "
                "communication"
            ),
        },
    ],
    "startup": [
        {
            "name": "Coding",
            "kind": "coding",
            "questions": 1,
            "focus": (
                "a practical, self-contained coding problem solved by reading stdin "
                "and printing to stdout"
            ),
        },
        {
            "name": "Machine Coding / Practical",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "building a small feature end-to-end: API/component design, edge "
                "cases, and pragmatic trade-offs under time pressure"
            ),
        },
        {
            "name": "System Design",
            "kind": "typed",
            "questions": 1,
            "focus": (
                "designing a small product system: requirements, architecture, "
                "data model, and scaling the pragmatic way"
            ),
        },
        {
            "name": "Culture Fit",
            "kind": "typed",
            "questions": 2,
            "focus": (
                "ownership, ambiguity, bias-to-action, and impact — grounded in "
                "the candidate's projects"
            ),
        },
    ],
    "other": [
        {
            "name": "Aptitude & Coding",
            "kind": "mcq",
            "questions": 3,
            "focus": "aptitude, reasoning, and coding / output-prediction MCQs",
        },
        {
            "name": "Technical",
            "kind": "typed",
            "questions": 2,
            "focus": "core CS fundamentals and the candidate's tech stack from the resume",
        },
        {
            "name": "System Design",
            "kind": "typed",
            "questions": 1,
            "focus": (
                "high-level system design: requirements, architecture, scaling, "
                "and trade-offs"
            ),
        },
        {
            "name": "Behavioural",
            "kind": "typed",
            "questions": 2,
            "focus": "behavioural questions grounded in the candidate's projects — STAR answers",
        },
    ],
}


@dataclass(frozen=True)
class SessionPlan:
    n_session: int
    soft_cap: int
    policy_version: str = SESSION_VERSION


@dataclass(frozen=True)
class ServeScheduleVerdict:
    refill_now: bool
    prefetch_transition: bool
    generate_next_page: bool
    periodic_refill: bool = False
    policy_version: str = SESSION_VERSION


@dataclass(frozen=True)
class InterviewRoundsPlan:
    category: str
    rounds: tuple[InterviewRound, ...]
    policy_version: str = SESSION_VERSION


# Prep progress blend (indexing vs cooking) — owned here, not in orchestration.
PREP_INDEX_WEIGHT = 0.35
PREP_COOK_WEIGHT = 0.65
PREP_PHASE_INDEXING = "indexing"
PREP_PHASE_COOKING = "cooking"

CookAction = Literal[
    "idle",
    "ingest",
    "transition_cook",
    "triage",
    "cook",
    "complete",
    "complete_cap",
]


@dataclass(frozen=True)
class PrepProgressVerdict:
    phase: str
    index_pct: int
    cook_pct: int
    overall_pct: int
    policy_version: str = SESSION_VERSION


@dataclass(frozen=True)
class CookScheduleVerdict:
    action: CookAction
    page: int | None = None
    cook_mode: str | None = None
    also_ingest: bool = False
    reason: str = ""
    policy_version: str = SESSION_VERSION


def plan_session(
    remaining_doc_items: int,
    *,
    soft_cap: int = SESSION_SOFT_DEFAULT,
    mode: str = "learn",
) -> SessionPlan:
    base = max(1, int(soft_cap))
    soft = pick(
        (mode or "learn").lower() == "test",
        lambda: max(base, 25),
        lambda: base,
    )
    rem = max(0, int(remaining_doc_items))
    return SessionPlan(n_session=choose(bool(rem), min(rem, soft), 0), soft_cap=soft)


@dataclass(frozen=True)
class SessionBreakVerdict:
    should_break: bool
    reason: str
    policy_version: str = SESSION_VERSION


_BREAK_RULES = (
    Rule(when=(Pred("no_cap", "truthy"),), action="no_cap", extras={"should_break": False}),
    Rule(when=(Pred("full", "truthy"),), action="session_full", extras={"should_break": True}),
    Rule(when=(), action="in_session", extras={"should_break": False}),
)
_BACKGROUND_COOK_RULES = (
    Rule(
        when=(Pred("indexing", "truthy"), Pred("all_indexed", "falsey"), Pred("has_ingest", "truthy")),
        action="ingest",
        extras={"reason": "index_missing"},
    ),
    Rule(
        when=(Pred("indexing", "truthy"), Pred("all_indexed", "falsey")),
        action="idle",
        extras={"reason": "wait_index"},
    ),
    Rule(
        when=(Pred("indexing", "truthy"), Pred("all_indexed", "truthy")),
        action="transition_cook",
        extras={"reason": "index_done"},
    ),
    Rule(when=(Pred("has_ingest", "truthy"),), action="ingest", extras={"reason": "window_missing"}),
    Rule(
        when=(Pred("has_triage", "truthy"),),
        action="triage",
        extras={"reason": "needs_triage", "use_triage": True},
    ),
    Rule(when=(Pred("no_cook", "truthy"),), action="complete", extras={"reason": "no_cook_page"}),
    Rule(
        when=(Pred("at_cap", "truthy"),),
        action="complete_cap",
        extras={"reason": "cap_reached", "use_cook": True},
    ),
    Rule(
        when=(Pred("page_done", "truthy"),),
        action="complete",
        extras={"reason": "page_budget_met", "use_cook": True},
    ),
    Rule(when=(), action="cook", extras={"reason": "cook_page", "use_cook": True}),
)
_EDITION_TICK_RULES = (
    Rule(
        when=(Pred("no_cook", "truthy"), Pred("has_triage", "truthy")),
        action="triage",
        extras={"reason": "edition_triage", "use_triage": True, "also": "missing"},
    ),
    Rule(
        when=(Pred("no_cook", "truthy"), Pred("has_ingest", "truthy")),
        action="ingest",
        extras={"reason": "edition_ingest", "also_true": True},
    ),
    Rule(when=(Pred("no_cook", "truthy"),), action="idle", extras={"reason": "edition_idle"}),
    Rule(
        when=(Pred("budget_met", "truthy"), Pred("has_ingest", "truthy")),
        action="ingest",
        extras={"reason": "edition_budget_met", "also_true": True},
    ),
    Rule(
        when=(Pred("budget_met", "truthy"),),
        action="idle",
        extras={"reason": "edition_budget_met", "use_cook": True, "keep_mode": True},
    ),
    Rule(
        when=(),
        action="cook",
        extras={"reason": "edition_cook", "use_cook": True, "keep_mode": True, "also": "missing"},
    ),
)
_PAGE_COMPLETE_RULES = (
    Rule(
        when=(Pred("non_content", "truthy"),),
        action="non_content",
        extras={"complete": True},
    ),
    Rule(
        when=(Pred("has_next_card", "truthy"),),
        action="unanswered",
        extras={"complete": False},
    ),
    Rule(
        when=(Pred("served_done", "truthy"),),
        action="served_answered",
        extras={"complete": True},
    ),
    Rule(
        when=(Pred("none_gen", "truthy"),),
        action="none_generated",
        extras={"complete": False},
    ),
    Rule(
        when=(Pred("still_cooking", "truthy"),),
        action="still_cooking",
        extras={"complete": False},
    ),
    Rule(
        when=(Pred("coverage_done", "truthy"),),
        action="coverage_done",
        extras={"complete": True},
    ),
    Rule(when=(Pred("met", "truthy"),), action="budget_met", extras={"complete": True}),
    Rule(when=(), action="under_budget", extras={"complete": False}),
)
_SHAPE_RULES = (
    Rule(when=(Pred("bad_mcq", "truthy"),), action="typed"),
    Rule(when=(Pred("bad_coding", "truthy"),), action="coding_fallback"),
    Rule(when=(), action="keep"),
)
_PAGE_COOK_RULES = (
    Rule(when=(Pred("skip", "truthy"),), action="idle", extras={"reason": "skip_page"}),
    Rule(
        when=(Pred("learn", "truthy"), Pred("no_learn_budget", "truthy")),
        action="idle",
        extras={"reason": "no_learn_budget"},
    ),
    Rule(
        when=(Pred("learn", "truthy"), Pred("learn_needs_cook", "truthy")),
        action="cook",
        extras={"reason": "edition_learn", "cook_mode": "learn"},
    ),
    Rule(when=(Pred("learn", "truthy"),), action="idle", extras={"reason": "learn_met"}),
    Rule(when=(Pred("learn_unmet", "truthy"),), action="idle", extras={"reason": "learn_first"}),
    Rule(
        when=(Pred("no_test_budget", "truthy"),),
        action="idle",
        extras={"reason": "no_test_budget"},
    ),
    Rule(
        when=(Pred("test_open", "truthy"),),
        action="cook",
        extras={"reason": "edition_test", "cook_mode": "test"},
    ),
    Rule(when=(), action="idle", extras={"reason": "test_met"}),
)
_TRANSITION_RULES = (
    Rule(when=(Pred("has_next", "falsey"),), action="none"),
    Rule(when=(Pred("next_has_coverage", "falsey"),), action="triage"),
    Rule(when=(), action="maybe_cook"),
)
_PAGE_ADVANCE_RULES = (
    Rule(when=(Pred("has_coverage", "falsey"),), action="triage"),
    Rule(when=(Pred("none_gen", "truthy"),), action="cook_first_batch"),
    Rule(when=(Pred("rag_ready", "falsey"),), action="ingest_rag"),
    Rule(when=(), action="noop"),
)
_HUB_HEAL_RULES = (
    Rule(when=(Pred("reingest", "truthy"),), action="reingest"),
    Rule(when=(Pred("recook", "truthy"),), action="recook"),
    Rule(when=(), action="idle"),
)


def evaluate_session_break(*, session_items: int, n_session: int) -> SessionBreakVerdict:
    """Soft break when the learner has filled this session's planned slice."""
    cap = max(0, int(n_session))
    items = max(0, int(session_items))
    hit = first_match(
        _BREAK_RULES,
        {"no_cap": cap <= 0, "full": items >= cap},
    )
    return SessionBreakVerdict(bool(hit.extras["should_break"]), hit.action)


def evaluate_serve_schedule(
    *,
    ready_count: int | None = None,
    answered_on_page: int | None = None,
    page_budget: int | None = None,
    low_water: int = READY_LOW_WATER,
    prefetch_ratio: float = TRANSITION_PREFETCH_RATIO,
    generation_ratio: float = TRANSITION_GENERATION_RATIO,
    refill_after_answered: int = REFILL_AFTER_ANSWERED,
) -> ServeScheduleVerdict:
    """When to refill the ready pool / prefetch next page / kick early generation."""
    refill = ready_count is not None and int(ready_count) < max(0, int(low_water))
    has_page = (
        answered_on_page is not None
        and page_budget is not None
        and int(page_budget) > 0
    )
    ratio = pick(has_page, lambda: int(answered_on_page) / int(page_budget), lambda: 0.0)
    prefetch = has_page and ratio > float(prefetch_ratio)
    generate = has_page and ratio >= float(generation_ratio)
    cadence = int(refill_after_answered)
    periodic = (
        answered_on_page is not None
        and cadence > 0
        and int(answered_on_page) > 0
        and int(answered_on_page) % cadence == 0
    )
    return ServeScheduleVerdict(
        refill_now=refill,
        prefetch_transition=prefetch,
        generate_next_page=generate,
        periodic_refill=periodic,
    )


def plan_interview_rounds(category: str | None) -> InterviewRoundsPlan:
    """Resolve mock-interview round schedule for a company category."""
    raw = (category or "").strip().lower()
    cat = choose(raw in INTERVIEW_ROUND_PLANS, raw, "other")
    rounds = tuple(dict(r) for r in INTERVIEW_ROUND_PLANS[cat])
    return InterviewRoundsPlan(category=cat, rounds=rounds)


def evaluate_prep_progress(
    *,
    index_pct: int,
    cook_pct: int,
    phase: str,
    has_study_pages: bool,
) -> PrepProgressVerdict:
    """Index/cook blend for the background-prep modal. Orchestration loads percents."""
    def with_pages() -> PrepProgressVerdict:
        idx = max(0, min(100, int(index_pct)))
        cook = max(0, min(100, int(cook_pct)))
        base = phase or PREP_PHASE_INDEXING
        resolved = pick(
            base == PREP_PHASE_INDEXING and idx >= 100,
            lambda: PREP_PHASE_COOKING,
            lambda: base,
        )
        overall = pick(
            resolved == PREP_PHASE_INDEXING,
            lambda: idx,
            lambda: int(idx * PREP_INDEX_WEIGHT + cook * PREP_COOK_WEIGHT),
        )
        return PrepProgressVerdict(
            phase=resolved,
            index_pct=idx,
            cook_pct=cook,
            overall_pct=min(100, overall),
        )

    return pick(
        not has_study_pages,
        lambda: PrepProgressVerdict(
            phase=PREP_PHASE_INDEXING,
            index_pct=0,
            cook_pct=0,
            overall_pct=0,
        ),
        with_pages,
    )


def evaluate_background_cook_tick(
    *,
    phase: str,
    all_indexed: bool,
    has_ingest_missing: bool,
    triage_page: int | None,
    cook_page: int | None,
    total_generated: int,
    max_questions: int,
    remaining_on_page: int | None,
) -> CookScheduleVerdict:
    """Next background-prep cook step. DB lookups stay in orchestration."""
    remaining = pick(remaining_on_page is None, lambda: 0, lambda: int(remaining_on_page))
    hit = first_match(
        _BACKGROUND_COOK_RULES,
        {
            "indexing": phase == PREP_PHASE_INDEXING,
            "all_indexed": all_indexed,
            "has_ingest": has_ingest_missing,
            "has_triage": triage_page is not None,
            "no_cook": cook_page is None,
            "at_cap": int(total_generated) >= max(0, int(max_questions)),
            "page_done": remaining <= 0,
        },
    )
    page = pick(
        bool(hit.extras.get("use_triage")),
        lambda: triage_page,
        lambda: pick(bool(hit.extras.get("use_cook")), lambda: cook_page, lambda: None),
    )
    action: CookAction = hit.action  # type: ignore[assignment]
    return CookScheduleVerdict(action=action, page=page, reason=str(hit.extras["reason"]))


def evaluate_newspaper_edition_tick(
    *,
    has_ingest_missing: bool,
    cook_page: int | None,
    cook_mode: str | None,
    remaining: int,
    triage_page: int | None,
) -> CookScheduleVerdict:
    """Full-edition newspaper cook priority: ingest in parallel, cook before triage."""
    hit = first_match(
        _EDITION_TICK_RULES,
        {
            "no_cook": cook_page is None,
            "has_triage": triage_page is not None,
            "has_ingest": has_ingest_missing,
            "budget_met": int(remaining) <= 0,
        },
    )
    also_ingest = pick(
        bool(hit.extras.get("also_true")),
        lambda: True,
        lambda: pick(hit.extras.get("also") == "missing", lambda: has_ingest_missing, lambda: False),
    )
    page = pick(
        bool(hit.extras.get("use_triage")),
        lambda: triage_page,
        lambda: pick(bool(hit.extras.get("use_cook")), lambda: cook_page, lambda: None),
    )
    mode = pick(bool(hit.extras.get("keep_mode")), lambda: cook_mode, lambda: None)
    action: CookAction = hit.action  # type: ignore[assignment]
    return CookScheduleVerdict(
        action=action,
        page=page,
        cook_mode=mode,
        also_ingest=also_ingest,
        reason=str(hit.extras["reason"]),
    )


@dataclass(frozen=True)
class PageCompleteVerdict:
    complete: bool
    reason: str
    policy_version: str = SESSION_VERSION


def evaluate_page_complete(
    *,
    non_content: bool,
    has_next_card: bool,
    all_served_answered: bool,
    has_active_generate: bool,
    generated: int,
    budget: int,
    coverage_done: bool,
    generation_pending: bool,
) -> PageCompleteVerdict:
    """When the current study page is done and the queue may advance."""
    hit = first_match(
        _PAGE_COMPLETE_RULES,
        {
            "non_content": non_content,
            "has_next_card": has_next_card,
            "served_done": all_served_answered and not has_active_generate,
            "none_gen": int(generated) <= 0,
            "still_cooking": generation_pending and not coverage_done and int(generated) < int(budget),
            "coverage_done": coverage_done,
            "met": int(generated) >= int(budget),
        },
    )
    return PageCompleteVerdict(bool(hit.extras["complete"]), hit.action)


def evaluate_newspaper_learn_complete(
    *,
    learn_pool_count: int,
    all_learn_answered: bool,
    generation_pending: bool,
) -> bool:
    """Edition Learn pool is finished (no pending cook)."""
    return learn_pool_count > 0 and all_learn_answered and not generation_pending


def evaluate_newspaper_learn_complete_counts(
    *,
    answered_count: int,
    pool_count: int,
    generation_pending: bool = False,
) -> bool:
    """Hub display: Learn complete from answered vs pool counts (no ID set)."""
    total = max(0, int(pool_count))
    answered = max(0, int(answered_count))
    return evaluate_newspaper_learn_complete(
        learn_pool_count=total,
        all_learn_answered=answered >= total,
        generation_pending=generation_pending,
    )


def evaluate_newspaper_catalog_ready(
    *,
    is_newspaper: bool,
    has_coverage: bool,
    budget: int,
    non_content: bool,
    page1_mcq_count: int,
) -> bool:
    """When a newspaper edition can leave indexing in the catalog."""
    return bool(is_newspaper) and bool(has_coverage) and (
        int(budget) <= 0 or non_content or int(page1_mcq_count) >= 1
    )


def plan_interview_question_shape(
    *,
    planned_kind: str,
    mcq_valid: bool,
    coding_has_tests: bool,
) -> str:
    """Degrade a broken MCQ to typed; keep coding rounds via fallback bank."""
    hit = first_match(
        _SHAPE_RULES,
        {
            "bad_mcq": planned_kind == "mcq" and not mcq_valid,
            "bad_coding": planned_kind == "coding" and not coding_has_tests,
        },
    )
    return choose(hit.action == "keep", planned_kind, hit.action)


@dataclass(frozen=True)
class InterviewGenContract:
    schema: str
    rules: str
    kind: str
    max_options: int = 0
    max_tests: int = 0
    policy_version: str = SESSION_VERSION


_INTERVIEW_TYPED_CONTRACT = InterviewGenContract(
    schema='{"question":"..."}',
    rules="An open-ended question the candidate answers by typing. No options.",
    kind="typed",
)
_INTERVIEW_CONTRACTS = {
    "mcq": InterviewGenContract(
        schema=(
            '{"question":"...","options":["a","b","c","d"],"correct_index":0,'
            '"explanation":"why the correct option is right"}'
        ),
        rules="Exactly 4 options, exactly one correct. correct_index is 0-3.",
        kind="mcq",
        max_options=INTERVIEW_MCQ_MAX_OPTIONS,
    ),
    "coding": InterviewGenContract(
        schema=(
            '{"question":"full problem statement incl. the exact stdin format and expected stdout format",'
            '"starter_code":"a runnable Python 3 stub reading stdin","language_id":71,'
            '"tests":[{"stdin":"...","expected_output":"..."}],'
            '"explanation":"the intended approach"}'
        ),
        rules=(
            "A self-contained coding problem the candidate solves by reading stdin and printing to "
            "stdout (no function signatures, a full program). Give 3-5 tests with EXACT stdin and the "
            "EXACT expected stdout (no trailing prose). starter_code must be valid Python 3 for "
            "language_id 71. Keep it solvable in ~10 minutes."
        ),
        kind="coding",
        max_tests=INTERVIEW_CODING_MAX_TESTS,
    ),
}


def plan_interview_gen_contract(kind: str) -> InterviewGenContract:
    """JSON schema + writer rules for the next interview question kind."""
    k = (kind or "").strip().lower()
    return _INTERVIEW_CONTRACTS.get(k, _INTERVIEW_TYPED_CONTRACT)


def pick_interview_coding_fallback(
    asked_count: int,
    bank: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Rotate the built-in coding bank when the LLM produced no tests."""
    return pick(not bank, lambda: {}, lambda: dict(bank[int(asked_count) % len(bank)]))


def evaluate_document_complete(
    *,
    page_complete: bool,
    on_last_page: bool,
    newspaper: bool,
    generation_pending: bool,
) -> bool:
    """Whole study range is done; newspaper waits for in-flight cook."""
    return bool(page_complete) and bool(on_last_page) and not (newspaper and generation_pending)


def evaluate_page_prep_ready(
    *,
    has_coverage: bool,
    non_content: bool,
    budget: int,
    generated: int,
    coverage_complete: bool,
) -> bool:
    """Background-prep page is cooked enough to count as ready."""
    return bool(has_coverage) and (
        non_content or int(budget) <= 0 or int(generated) >= int(budget) or coverage_complete
    )


@dataclass(frozen=True)
class NewspaperPageCookSignal:
    page: int
    has_active_generate: bool
    has_coverage: bool
    non_content: bool
    learn_budget: int
    learn_generated: int
    coverage_complete: bool
    test_budget: int
    test_generated: int


def evaluate_newspaper_page_cook(
    page: NewspaperPageCookSignal,
    *,
    phase: Literal["learn", "test"],
) -> CookScheduleVerdict:
    """Whether this edition page should cook in the current learn/test pass."""
    hit = first_match(
        _PAGE_COOK_RULES,
        {
            "skip": page.has_active_generate or not page.has_coverage or page.non_content,
            "learn": phase == "learn",
            "no_learn_budget": int(page.learn_budget) <= 0,
            "learn_needs_cook": not page.coverage_complete
            or int(page.learn_generated) < int(page.learn_budget),
            "learn_unmet": int(page.learn_budget) > 0
            and int(page.learn_generated) < int(page.learn_budget),
            "no_test_budget": int(page.test_budget) <= 0,
            "test_open": int(page.test_generated) < int(page.test_budget),
        },
    )
    action: CookAction = hit.action  # type: ignore[assignment]
    return CookScheduleVerdict(
        action=action,
        page=page.page,
        cook_mode=hit.extras.get("cook_mode"),
        reason=str(hit.extras["reason"]),
    )


def plan_newspaper_cook_target(
    pages: Sequence[NewspaperPageCookSignal],
) -> CookScheduleVerdict:
    """Learn cook across pages first, then test cook."""

    def first_cook(phase: Literal["learn", "test"]) -> CookScheduleVerdict | None:
        return next(
            filter(
                lambda v: v.action == "cook",
                (evaluate_newspaper_page_cook(p, phase=phase) for p in pages),
            ),
            None,
        )

    learn = first_cook("learn")
    return pick(
        learn is not None,
        lambda: learn,
        lambda: pick(
            (test := first_cook("test")) is not None,
            lambda: test,
            lambda: CookScheduleVerdict(action="idle", reason="edition_cook_idle"),
        ),
    )


def evaluate_newspaper_triage_complete(
    *,
    has_any_coverage: bool,
    questions_generated: int,
) -> bool:
    """Newspaper Learn UI: triage is done if any page landed or MCQs exist."""
    return bool(has_any_coverage) or int(questions_generated) > 0


def evaluate_learn_cook_coverage_close(*, hit_budget: bool, serve_mode: str) -> bool:
    """Learn cook may stamp coverage complete once N_page is filled."""
    return bool(hit_budget) and (serve_mode or "") == "learn"


def evaluate_empty_batch_coverage_close(
    *,
    saved: int,
    start_sequence: int,
    batch_size: int,
    budget: int,
    has_targets: bool,
    has_aspects: bool,
) -> bool:
    """Close coverage when a zero-save batch already sits at budget with no targets."""
    return (
        int(saved) == 0
        and int(start_sequence) + int(batch_size) >= int(budget)
        and not has_targets
        and bool(has_aspects)
    )


@dataclass(frozen=True)
class AuxCookSpawnVerdict:
    spawn_coding: bool
    spawn_debug: bool
    policy_version: str = SESSION_VERSION


def alias_debuggable(*, programmable: bool, debuggable: bool | None) -> bool:
    """Debug cook follows an explicit flag, else programmable pages."""
    return bool(debuggable or programmable)


def evaluate_auxiliary_cook_spawn(
    *,
    programmable: bool,
    debuggable: bool,
) -> AuxCookSpawnVerdict:
    """Whether triage should spawn coding / debug cooks besides MCQs."""
    return AuxCookSpawnVerdict(
        spawn_coding=bool(programmable),
        spawn_debug=bool(debuggable),
    )


def plan_first_cook_batch(*, budget: int, first_batch: int = 1) -> int:
    """Zero-wait first MCQ batch; never larger than remaining N_page."""
    n = max(0, int(budget))
    return min(max(1, int(first_batch)), n)


def evaluate_background_first_batch(
    *,
    budget: int,
    generated: int,
    first_batch: int = 1,
) -> int:
    """Kick the first cook after background triage, or 0 to skip."""
    return pick(
        int(generated) > 0,
        lambda: 0,
        lambda: plan_first_cook_batch(budget=budget, first_batch=first_batch),
    )


@dataclass(frozen=True)
class TransitionNextPlan:
    triage_next: bool
    cook_next: bool
    policy_version: str = SESSION_VERSION


def plan_transition_next(
    *,
    has_next: bool,
    next_has_coverage: bool,
    next_generated: int,
    generate_next_page: bool,
    current_budget: int,
) -> TransitionNextPlan:
    """Page-turn: triage the next page, or cook its first batch."""
    hit = first_match(
        _TRANSITION_RULES,
        {"has_next": has_next, "next_has_coverage": next_has_coverage},
    )
    allow = bool(generate_next_page) and int(current_budget) > 0
    cook = allow and int(next_generated) == 0
    return apply(
        hit.action,
        {
            "none": lambda: TransitionNextPlan(False, False),
            "triage": lambda: TransitionNextPlan(True, False),
            "maybe_cook": lambda: TransitionNextPlan(False, cook),
        },
    )


def evaluate_serial_cook_fallback(*, saved: int, has_targets: bool) -> bool:
    """When a parallel batch saves nothing, retry remaining aspects serially."""
    return int(saved) == 0 and bool(has_targets)


def plan_eager_triage_pages(
    *,
    from_page: int,
    study_pages: Sequence[int],
    lookahead: int = EAGER_TRIAGE_LOOKAHEAD,
    covered_pages: Sequence[int] = (),
    active_triage_pages: Sequence[int] = (),
) -> tuple[int, ...]:
    """Rolling lookahead: pages after the reader that still need triage."""
    study = list(study_pages)
    return pick(
        from_page not in study,
        lambda: (),
        lambda: tuple(
            filter(
                lambda p: p not in set(covered_pages) and p not in set(active_triage_pages),
                study[
                    study.index(from_page) + 1 : study.index(from_page)
                    + 1
                    + max(0, int(lookahead))
                ],
            )
        ),
    )


PageAdvanceAction = Literal["triage", "cook_first_batch", "ingest_rag", "noop"]


@dataclass(frozen=True)
class PageAdvancePlan:
    action: PageAdvanceAction
    policy_version: str = SESSION_VERSION


def plan_page_advance_next(
    *,
    has_coverage: bool,
    generated: int,
    rag_ready: bool,
) -> PageAdvancePlan:
    """After a page turn: triage, first cook, RAG ingest, or nothing."""
    hit = first_match(
        _PAGE_ADVANCE_RULES,
        {
            "has_coverage": has_coverage,
            "none_gen": int(generated) <= 0,
            "rag_ready": rag_ready,
        },
    )
    action: PageAdvanceAction = hit.action  # type: ignore[assignment]
    return PageAdvancePlan(action)


def should_require_rag_on_page_advance(*, has_coverage: bool, generated: int) -> bool:
    """Only wait on RAG ingest when the new page already has cooked questions."""
    return bool(has_coverage) and int(generated) > 0


NEWSPAPER_INGEST_BATCH = 8
NEWSPAPER_RECOVERY_EDITION_LIMIT = 10
NEWSPAPER_EDITION_QUESTIONS_DEFAULT = 40
NEWSPAPER_EDITION_QUESTIONS_MAX = 80


def plan_newspaper_ingest_batch(
    missing: Sequence[int],
    *,
    batch_size: int = NEWSPAPER_INGEST_BATCH,
) -> tuple[int, ...]:
    """Cap missing-page ingest so one edition tick cannot flood the queue."""
    cap = max(1, int(batch_size))
    return tuple(int(p) for p in list(missing)[:cap])


def plan_newspaper_recovery_batch() -> int:
    """How many stuck ready-doc editions one uncooked recovery tick may re-enqueue."""
    return NEWSPAPER_RECOVERY_EDITION_LIMIT


def plan_newspaper_edition_questions_limit(requested: int | None = None) -> int:
    """How many edition MCQs newspaper practice may list (default + hard max)."""
    n = pick(
        requested is None,
        lambda: NEWSPAPER_EDITION_QUESTIONS_DEFAULT,
        lambda: int(requested),
    )
    return max(1, min(n, NEWSPAPER_EDITION_QUESTIONS_MAX))


def evaluate_edition_practice_window(edition_date: date, *, cutoff: date) -> bool:
    """Learners may open editions on or after the retention cutoff."""
    return edition_date >= cutoff


@dataclass(frozen=True)
class NewspaperServeScope:
    stats_from_edition: bool
    selection_from_page: bool
    load_mode_pools: bool
    edition_budget: bool
    policy_version: str = SESSION_VERSION


def plan_newspaper_serve_scope(*, newspaper: bool) -> NewspaperServeScope:
    """Edition-wide stats; page-scoped adaptive pick so chrome does not jump."""
    return NewspaperServeScope(*([newspaper] * 4))


def plan_refill_batch(
    *,
    remaining: int,
    batch_size: int = REFILL_BATCH_SIZE,
) -> int:
    """Rolling cook chunk: never one job for the entire remaining N_page."""
    return max(0, min(int(batch_size), max(0, int(remaining))))


NewspaperHealAction = Literal["reingest", "recook", "idle"]
NewspaperUncookedFollowup = Literal["wait", "promote_ready"]


def plan_newspaper_hub_heal(*, doc_status: str) -> NewspaperHealAction:
    """Hub/schedule: stuck edition re-ingest vs recook vs ignore."""
    status = (doc_status or "").strip().lower()
    hit = first_match(
        _HUB_HEAL_RULES,
        {
            "reingest": status in ("indexing", "pending"),
            "recook": status == "ready",
        },
    )
    action: NewspaperHealAction = hit.action  # type: ignore[assignment]
    return action


def plan_newspaper_uncooked_followup(*, cook_enqueued: bool) -> NewspaperUncookedFollowup:
    """After a ready-doc recook attempt: wait for the job or promote the catalog."""
    return choose(cook_enqueued, "wait", "promote_ready")


@dataclass(frozen=True)
class GenerationBatchOutcome:
    activity_status: Literal["succeeded", "failed"]
    error_summary: str | None
    error_code: str | None
    policy_version: str = SESSION_VERSION


def evaluate_generation_batch_outcome(
    *,
    saved: int,
    has_targets: bool,
) -> GenerationBatchOutcome:
    """Cook batch: failed only when targets existed and quality saved nothing."""
    return pick(
        int(saved) <= 0 and bool(has_targets),
        lambda: GenerationBatchOutcome(
            "failed",
            "No questions passed quality gates",
            "generation_empty",
        ),
        lambda: GenerationBatchOutcome("succeeded", None, None),
    )


@dataclass(frozen=True)
class InterviewAdvancePlan:
    complete: bool
    round_index: int
    q_in_round: int
    policy_version: str = SESSION_VERSION


def plan_interview_advance(
    *,
    round_index: int,
    q_in_round: int,
    round_question_count: int,
    round_count: int,
) -> InterviewAdvancePlan:
    """Next question in round, else next round, else the interview is complete."""
    qi_next = int(q_in_round) + 1
    overflow = qi_next >= int(round_question_count)
    ri = int(round_index) + choose(overflow, 1, 0)
    qi = choose(overflow, 0, qi_next)
    return pick(
        ri >= int(round_count),
        lambda: InterviewAdvancePlan(True, int(round_count), 0),
        lambda: InterviewAdvancePlan(False, ri, qi),
    )


@dataclass(frozen=True)
class StudyRangeHealPlan:
    clear_selected_range: bool
    reset_to_pending: bool
    policy_version: str = SESSION_VERSION


def plan_study_range_heal(
    *,
    counted_pages: int,
    selected_range: Mapping[str, Any] | None,
    doc_status: str,
) -> StudyRangeHealPlan:
    """DOCX page-count heal: drop a stale auto-selected [1] when more pages exist."""
    def heal() -> StudyRangeHealPlan:
        pages = selected_range.get("pages")
        only_page_one = (isinstance(pages, list) and pages == [1]) or (
            int(selected_range.get("from") or 0) == 1
            and int(selected_range.get("to") or 0) == 1
            and not pages
        )
        return pick(
            not only_page_one,
            lambda: StudyRangeHealPlan(False, False),
            lambda: StudyRangeHealPlan(
                True,
                (doc_status or "").strip().lower() in {"ready", "indexing"},
            ),
        )

    return pick(
        int(counted_pages) <= 1 or not selected_range,
        lambda: StudyRangeHealPlan(False, False),
        heal,
    )


AuxiliaryKind = Literal["flashcards", "topics", "memory_palace", "quiz"]


@dataclass(frozen=True)
class AuxiliaryArtifactCap:
    kind: AuxiliaryKind
    min_count: int
    max_count: int
    default_count: int
    policy_version: str = SESSION_VERSION


def plan_auxiliary_artifact_cap(kind: AuxiliaryKind) -> AuxiliaryArtifactCap:
    """How many flashcards, topics, palace stations, or quiz items to keep."""
    caps = {
        "flashcards": AuxiliaryArtifactCap("flashcards", 0, FLASHCARD_MAX, FLASHCARD_MAX),
        "topics": AuxiliaryArtifactCap("topics", 0, TOPIC_OUTLINE_MAX, TOPIC_OUTLINE_MAX),
        "memory_palace": AuxiliaryArtifactCap(
            "memory_palace",
            MEMORY_PALACE_MIN_STATIONS,
            MEMORY_PALACE_MAX_STATIONS,
            MEMORY_PALACE_MAX_STATIONS,
        ),
        "quiz": AuxiliaryArtifactCap("quiz", 1, QUIZ_MAX_QUESTIONS, QUIZ_DEFAULT_COUNT),
    }
    return caps.get(kind, caps["quiz"])


def clamp_auxiliary_count(kind: AuxiliaryKind, requested: int | None) -> int:
    """Clamp a requested artifact count into the session cap."""
    plan = plan_auxiliary_artifact_cap(kind)
    n = pick(bool(requested), lambda: int(requested), lambda: plan.default_count)
    return max(plan.min_count, min(n, plan.max_count))


@dataclass(frozen=True)
class AuxiliaryOutputVerdict:
    keep: bool
    reason: str
    policy_version: str = SESSION_VERSION


def evaluate_auxiliary_output(kind: AuxiliaryKind, count: int) -> AuxiliaryOutputVerdict:
    """Keep or reject a generated auxiliary artifact after count is known."""
    cap = plan_auxiliary_artifact_cap(kind)
    return pick(
        int(count) < cap.min_count,
        lambda: AuxiliaryOutputVerdict(False, "below_min"),
        lambda: AuxiliaryOutputVerdict(True, "ok"),
    )


AuxiliaryMapKind = Literal[
    "flashcards", "topics", "memory_palace", "notes", "summarize", "audiobook"
]
AUXILIARY_MAP_CONCURRENCY = 6
AUDIOBOOK_MAP_CONCURRENCY = 4
AUXILIARY_CHUNK_LOAD_LIMIT = 200


def plan_auxiliary_map_concurrency(kind: AuxiliaryMapKind) -> int:
    """How many chunk-map LLM calls may run in parallel for one artifact cook."""
    return choose(kind == "audiobook", AUDIOBOOK_MAP_CONCURRENCY, AUXILIARY_MAP_CONCURRENCY)


def plan_auxiliary_chunk_load_limit() -> int:
    """How many stored chunks feed one auxiliary cook (summarize, notes, quiz, …)."""
    return AUXILIARY_CHUNK_LOAD_LIMIT


@dataclass(frozen=True)
class IngestRecoverySchedule:
    indexing_grace_minutes: int
    prepping_grace_minutes: int
    indexing_batch: int
    prepping_batch: int
    policy_version: str = SESSION_VERSION


def plan_ingest_recovery_schedule() -> IngestRecoverySchedule:
    """When indexing/prepping looks stuck, how long to wait and how many to heal."""
    return IngestRecoverySchedule(
        INDEXING_RECOVERY_GRACE_MINUTES,
        PREPPING_RECOVERY_GRACE_MINUTES,
        INDEXING_RECOVERY_BATCH,
        PREPPING_RECOVERY_BATCH,
    )


LearnerListKind = Literal["saved_notes", "brainstorm"]


def plan_learner_list_cap(kind: LearnerListKind) -> int:
    """How many saved notes or brainstorm ideas a learner may keep per source."""
    return choose(kind == "saved_notes", SAVED_NOTES_MAX, BRAINSTORM_IDEAS_MAX)


def plan_brainstorm_tree_depth() -> int:
    """How deep a brainstorm export tree may nest."""
    return BRAINSTORM_TREE_DEPTH_MAX


@dataclass(frozen=True)
class AuxiliaryFieldCaps:
    kind: AuxiliaryKind
    fields: Mapping[str, int]
    policy_version: str = SESSION_VERSION

    def limit(self, name: str) -> int:
        return int(self.fields[name])


def plan_auxiliary_field_caps(kind: AuxiliaryKind) -> AuxiliaryFieldCaps:
    """Truncate generated artifact fields so a runaway model cannot store a wall of text."""
    fields = {
        "flashcards": AuxiliaryFieldCaps("flashcards", {"front": 400, "back": 600}),
        "topics": AuxiliaryFieldCaps("topics", {"title": 120, "summary": 300}),
        "memory_palace": AuxiliaryFieldCaps(
            "memory_palace",
            {
                "locus": 120,
                "term": 120,
                "fact": 400,
                "image": 600,
                "cue": 200,
                "setting": 160,
                "intro": 400,
            },
        ),
        "quiz": AuxiliaryFieldCaps(
            "quiz",
            {
                "prompt": 600,
                "explanation": 600,
                "answer": 1200,
                "options": 8,
                "pairs": 8,
            },
        ),
    }
    return fields.get(kind, fields["quiz"])


AuxiliaryGenMode = Literal["single_shot", "map_reduce"]


@dataclass(frozen=True)
class AuxiliaryGenerationPlan:
    mode: AuxiliaryGenMode
    single_shot_max_tokens: int
    policy_version: str = SESSION_VERSION


def plan_auxiliary_generation_strategy(
    token_count: int,
    *,
    single_shot_max_tokens: int | None = None,
) -> AuxiliaryGenerationPlan:
    """Single-shot vs map-reduce for notes, topics, flashcards, palace, summarize."""
    cap = pick(
        single_shot_max_tokens is None,
        lambda: SUMMARIZE_SINGLE_SHOT_MAX_TOKENS,
        lambda: int(single_shot_max_tokens),
    )
    return pick(
        int(token_count) <= cap,
        lambda: AuxiliaryGenerationPlan("single_shot", cap),
        lambda: AuxiliaryGenerationPlan("map_reduce", cap),
    )


def plan_option_coach_page_limit() -> int:
    """How many uncoached MCQs to warm option-feedback for on one page tick."""
    return OPTION_COACH_PAGE_LIMIT


def plan_grade_feedback_timeout() -> int:
    """Seconds to wait for live grade coaching before falling back to the stored explanation."""
    return GRADE_FEEDBACK_TIMEOUT_SECONDS


def evaluate_option_coach_eligible(*, is_multi: bool) -> bool:
    """Precomputed per-option coaching is only for single-answer MCQs."""
    return not bool(is_multi)


def plan_interview_asked_context() -> int:
    """How many already-asked interview questions to paste into the next prompt."""
    return INTERVIEW_ASKED_CONTEXT


@dataclass(frozen=True)
class BundleUploadPlan:
    min_files: int
    max_files: int
    policy_version: str = SESSION_VERSION


def plan_bundle_upload() -> BundleUploadPlan:
    """How many files a learner must (and may) combine into one source."""
    return BundleUploadPlan(BUNDLE_UPLOAD_MIN_FILES, BUNDLE_UPLOAD_MAX_FILES)


PREP_MODE_NOW = "now"
PREP_MODE_BACKGROUND = "background"


def evaluate_background_prep_active(
    *,
    has_doc: bool,
    prep_mode: str | None,
    prep_complete: bool,
) -> bool:
    """True while a document is still in background prep (not yet flipped complete)."""
    return bool(has_doc) and prep_mode == PREP_MODE_BACKGROUND and not prep_complete


def evaluate_prep_short_circuit(
    *,
    prep_complete: bool,
    prep_mode: str | None,
) -> bool:
    """Prep is done when flagged complete, or the document is not in background mode."""
    return bool(prep_complete) or prep_mode != PREP_MODE_BACKGROUND


def evaluate_capped_prep_promote(*, prep_complete: bool, status: str) -> bool:
    """Promote a cap-halted prep doc to ready so the learner can open the partial pool."""
    return bool(prep_complete) and status != "ready"


_DIGEST_ENQUEUE_RULES = (
    Rule(when=(Pred("cook_enabled", "falsey"),), action="skip_disabled"),
    Rule(when=(Pred("has_row", "falsey"),), action="skip_missing"),
    Rule(when=(Pred("already_published", "truthy"),), action="skip_published"),
    Rule(when=(Pred("has_post_id", "truthy"),), action="skip_has_post"),
    Rule(when=(Pred("may_enqueue", "falsey"),), action="skip_attempted"),
    Rule(when=(), action="enqueue"),
)


def evaluate_newspaper_digest_enqueue(
    *,
    cook_enabled: bool,
    has_row: bool,
    already_published: bool,
    has_post_id: bool,
    may_enqueue: bool,
) -> str:
    """Whether becoming catalog-ready should enqueue an edition digest cook."""
    return first_match(
        _DIGEST_ENQUEUE_RULES,
        {
            "cook_enabled": cook_enabled,
            "has_row": has_row,
            "already_published": already_published,
            "has_post_id": has_post_id,
            "may_enqueue": may_enqueue,
        },
    ).action


_LEARNER_IDENTITY_RULES = (
    Rule(when=(Pred("has_account", "truthy"),), action="account"),
    Rule(when=(Pred("has_guest", "truthy"),), action="guest"),
    Rule(when=(), action="none"),
)


def evaluate_learner_identity(
    *,
    account_id: Any,
    guest_id: str | None,
) -> str:
    """Which subject key scopes progress SQL: account, guest, or none."""
    return first_match(
        _LEARNER_IDENTITY_RULES,
        {"has_account": account_id is not None, "has_guest": bool(guest_id)},
    ).action


_TEST_DOWNGRADE_RULES = (
    Rule(
        when=(
            Pred("newspaper", "truthy"),
            Pred("test", "truthy"),
            Pred("learn_complete", "falsey"),
        ),
        action="learn",
    ),
    Rule(when=(), action="keep"),
)
_AUTO_ADVANCE_RULES = (
    Rule(when=(Pred("page_complete", "falsey"),), action="stay"),
    Rule(when=(Pred("at_last", "truthy"),), action="stay"),
    Rule(when=(), action="advance"),
)
_ADVANCE_PAGE_RULES = (
    Rule(
        when=(Pred("same_page", "truthy"), Pred("page_complete", "truthy")),
        action="advance",
    ),
    Rule(when=(), action="stay"),
)
_LEARN_STREAM_DELAY_RULES = (
    Rule(when=(Pred("document_complete", "truthy"),), action="done", extras={"delay": 0.0}),
    Rule(
        when=(Pred("has_current", "truthy"), Pred("generation_pending", "falsey")),
        action="idle_question",
        extras={"delay": 3.0},
    ),
    Rule(when=(Pred("generation_pending", "truthy"),), action="backoff"),
    Rule(when=(), action="poll", extras={"delay": 2.0}),
)


def evaluate_test_downgrade(
    *,
    newspaper: bool,
    serve_mode: str,
    learn_complete: bool,
) -> str:
    """Newspaper Test stays locked on Learn until the Learn pool is finished."""
    hit = first_match(
        _TEST_DOWNGRADE_RULES,
        {
            "newspaper": bool(newspaper),
            "test": serve_mode == "test",
            "learn_complete": bool(learn_complete),
        },
    )
    return pick(hit.action == "keep", lambda: serve_mode, lambda: hit.action)


def evaluate_learn_auto_advance(
    *,
    page_complete: bool,
    page: int,
    last_page: int,
) -> bool:
    """Learn queue may walk to the next study page when this page is done."""
    hit = first_match(
        _AUTO_ADVANCE_RULES,
        {
            "page_complete": bool(page_complete),
            "at_last": int(page) >= int(last_page),
        },
    )
    return hit.action == "advance"


def evaluate_advance_page(
    *,
    current_page: int,
    requested_page: int,
    page_complete: bool,
) -> bool:
    """POST advance only moves when the requested page is the complete current page."""
    hit = first_match(
        _ADVANCE_PAGE_RULES,
        {
            "same_page": int(current_page) == int(requested_page),
            "page_complete": bool(page_complete),
        },
    )
    return hit.action == "advance"


@dataclass(frozen=True)
class LearnStreamDelayPlan:
    action: str
    delay: float
    policy_version: str = SESSION_VERSION


def plan_learn_stream_delay(
    *,
    document_complete: bool,
    current_assertion_id: Any,
    generation_pending: bool,
    delay: float,
) -> LearnStreamDelayPlan:
    """SSE poll cadence while the Learn queue is still cooking."""
    hit = first_match(
        _LEARN_STREAM_DELAY_RULES,
        {
            "document_complete": bool(document_complete),
            "has_current": bool(current_assertion_id),
            "generation_pending": bool(generation_pending),
        },
    )
    next_delay = pick(
        hit.action == "backoff",
        lambda: min(8.0, float(delay) * 1.15),
        lambda: float(hit.extras["delay"]),
    )
    return LearnStreamDelayPlan(hit.action, next_delay)


_RAG_READY_COOK_RULES = (
    Rule(when=(Pred("background", "truthy"),), action="skip"),
    Rule(when=(), action="enqueue"),
)


def evaluate_rag_ready_cook_spawn(*, background_prep: bool) -> str:
    """After RAG window is ready: enqueue first cook, unless background-prep owns it."""
    return first_match(_RAG_READY_COOK_RULES, {"background": background_prep}).action


_EDITION_DISPATCH_RULES = (
    Rule(when=(Pred("action", "eq", "triage"), Pred("has_page", "truthy")), action="triage"),
    Rule(when=(Pred("action", "eq", "cook"), Pred("has_page", "truthy")), action="cook"),
    Rule(when=(), action="ingest_only"),
)

_PAGE_BATCH_ENQUEUE_RULES = (
    Rule(when=(Pred("no_remaining", "truthy"), Pred("is_current", "truthy")), action="clear_pending"),
    Rule(when=(Pred("no_remaining", "truthy"),), action="skip"),
    Rule(when=(Pred("active", "truthy"), Pred("is_current", "truthy")), action="mark_pending"),
    Rule(when=(Pred("active", "truthy"),), action="skip"),
    Rule(when=(), action="enqueue"),
)

_REFILL_DISPATCH_RULES = (
    Rule(when=(Pred("empty", "truthy"),), action="skip"),
    Rule(when=(), action="enqueue"),
)

_POOL_WAKE_RULES = (
    Rule(when=(Pred("missing", "truthy"),), action="skip"),
    Rule(when=(Pred("not_ready", "truthy"),), action="skip"),
    Rule(when=(Pred("no_text", "truthy"),), action="skip"),
    Rule(when=(), action="ok"),
)


def evaluate_newspaper_edition_dispatch(*, action: str, page: int | None) -> str:
    """Map an edition-tick verdict onto triage / cook / ingest-only effects."""
    return first_match(
        _EDITION_DISPATCH_RULES,
        {"action": action, "has_page": page is not None},
    ).action


def evaluate_page_batch_enqueue(
    *,
    remaining: int,
    is_current: bool,
    active: bool,
) -> str:
    """Wake / skip / enqueue the next generate batch for a page."""
    return first_match(
        _PAGE_BATCH_ENQUEUE_RULES,
        {
            "no_remaining": int(remaining) <= 0,
            "is_current": is_current,
            "active": active,
        },
    ).action


def evaluate_refill_dispatch(*, batch: int) -> str:
    """Skip enqueue when the refill batch is empty."""
    return first_match(_REFILL_DISPATCH_RULES, {"empty": int(batch) <= 0}).action


def evaluate_pool_wake(
    *,
    missing: bool,
    ready: bool,
    no_searchable_text: bool,
) -> str:
    """Whether ensure_question_pool should start work."""
    return first_match(
        _POOL_WAKE_RULES,
        {
            "missing": missing,
            "not_ready": not ready,
            "no_text": no_searchable_text,
        },
    ).action
