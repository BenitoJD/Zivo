"""Session Design Engine - soft serve-session length + pool schedule thresholds.

Design: docs/SESSION_DESIGN_ENGINE.md
Version: qb.session.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping, Sequence

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
    soft = max(1, int(soft_cap))
    # Test sessions allow a slightly longer soft slice.
    if (mode or "learn").lower() == "test":
        soft = max(soft, 25)
    rem = max(0, int(remaining_doc_items))
    return SessionPlan(n_session=min(rem, soft) if rem else 0, soft_cap=soft)


@dataclass(frozen=True)
class SessionBreakVerdict:
    should_break: bool
    reason: str
    policy_version: str = SESSION_VERSION


def evaluate_session_break(*, session_items: int, n_session: int) -> SessionBreakVerdict:
    """Soft break when the learner has filled this session's planned slice."""
    cap = max(0, int(n_session))
    items = max(0, int(session_items))
    if cap <= 0:
        return SessionBreakVerdict(False, "no_cap")
    if items >= cap:
        return SessionBreakVerdict(True, "session_full")
    return SessionBreakVerdict(False, "in_session")


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
    refill = False
    if ready_count is not None:
        refill = int(ready_count) < max(0, int(low_water))
    prefetch = False
    generate = False
    if (
        answered_on_page is not None
        and page_budget is not None
        and int(page_budget) > 0
    ):
        ratio = int(answered_on_page) / int(page_budget)
        prefetch = ratio > float(prefetch_ratio)
        generate = ratio >= float(generation_ratio)
    periodic = False
    cadence = int(refill_after_answered)
    if answered_on_page is not None and cadence > 0:
        answered = int(answered_on_page)
        periodic = answered > 0 and answered % cadence == 0
    return ServeScheduleVerdict(
        refill_now=refill,
        prefetch_transition=prefetch,
        generate_next_page=generate,
        periodic_refill=periodic,
    )


def plan_interview_rounds(category: str | None) -> InterviewRoundsPlan:
    """Resolve mock-interview round schedule for a company category."""
    cat = (category or "").strip().lower()
    if cat not in INTERVIEW_ROUND_PLANS:
        cat = "other"
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
    if not has_study_pages:
        return PrepProgressVerdict(
            phase=PREP_PHASE_INDEXING,
            index_pct=0,
            cook_pct=0,
            overall_pct=0,
        )
    idx = max(0, min(100, int(index_pct)))
    cook = max(0, min(100, int(cook_pct)))
    resolved = phase or PREP_PHASE_INDEXING
    if resolved == PREP_PHASE_INDEXING and idx >= 100:
        resolved = PREP_PHASE_COOKING
    if resolved == PREP_PHASE_INDEXING:
        overall = idx
    else:
        overall = int(idx * PREP_INDEX_WEIGHT + cook * PREP_COOK_WEIGHT)
    return PrepProgressVerdict(
        phase=resolved,
        index_pct=idx,
        cook_pct=cook,
        overall_pct=min(100, overall),
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
    if phase == PREP_PHASE_INDEXING and not all_indexed:
        if has_ingest_missing:
            return CookScheduleVerdict(action="ingest", reason="index_missing")
        return CookScheduleVerdict(action="idle", reason="wait_index")
    if phase == PREP_PHASE_INDEXING and all_indexed:
        return CookScheduleVerdict(action="transition_cook", reason="index_done")
    if has_ingest_missing:
        return CookScheduleVerdict(action="ingest", reason="window_missing")
    if triage_page is not None:
        return CookScheduleVerdict(action="triage", page=triage_page, reason="needs_triage")
    if cook_page is None:
        return CookScheduleVerdict(action="complete", reason="no_cook_page")
    if int(total_generated) >= max(0, int(max_questions)):
        return CookScheduleVerdict(action="complete_cap", page=cook_page, reason="cap_reached")
    remaining = 0 if remaining_on_page is None else int(remaining_on_page)
    if remaining <= 0:
        return CookScheduleVerdict(action="complete", page=cook_page, reason="page_budget_met")
    return CookScheduleVerdict(action="cook", page=cook_page, reason="cook_page")


def evaluate_newspaper_edition_tick(
    *,
    has_ingest_missing: bool,
    cook_page: int | None,
    cook_mode: str | None,
    remaining: int,
    triage_page: int | None,
) -> CookScheduleVerdict:
    """Full-edition newspaper cook priority: ingest in parallel, cook before triage."""
    if cook_page is None:
        if triage_page is not None:
            return CookScheduleVerdict(
                action="triage",
                page=triage_page,
                also_ingest=has_ingest_missing,
                reason="edition_triage",
            )
        if has_ingest_missing:
            return CookScheduleVerdict(action="ingest", also_ingest=True, reason="edition_ingest")
        return CookScheduleVerdict(action="idle", reason="edition_idle")
    if int(remaining) <= 0:
        if has_ingest_missing:
            return CookScheduleVerdict(action="ingest", also_ingest=True, reason="edition_budget_met")
        return CookScheduleVerdict(action="idle", page=cook_page, cook_mode=cook_mode, reason="edition_budget_met")
    return CookScheduleVerdict(
        action="cook",
        page=cook_page,
        cook_mode=cook_mode,
        also_ingest=has_ingest_missing,
        reason="edition_cook",
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
    if non_content:
        return PageCompleteVerdict(True, "non_content")
    if has_next_card:
        return PageCompleteVerdict(False, "unanswered")
    if all_served_answered and not has_active_generate:
        return PageCompleteVerdict(True, "served_answered")
    if int(generated) <= 0:
        return PageCompleteVerdict(False, "none_generated")
    if generation_pending and not coverage_done and int(generated) < int(budget):
        return PageCompleteVerdict(False, "still_cooking")
    if coverage_done:
        return PageCompleteVerdict(True, "coverage_done")
    met = int(generated) >= int(budget)
    return PageCompleteVerdict(met, "budget_met" if met else "under_budget")


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
    if not is_newspaper:
        return False
    if not has_coverage:
        return False
    if int(budget) <= 0 or non_content:
        return True
    return int(page1_mcq_count) >= 1


def plan_interview_question_shape(
    *,
    planned_kind: str,
    mcq_valid: bool,
    coding_has_tests: bool,
) -> str:
    """Degrade a broken MCQ to typed; keep coding rounds via fallback bank."""
    if planned_kind == "mcq" and not mcq_valid:
        return "typed"
    if planned_kind == "coding" and not coding_has_tests:
        return "coding_fallback"
    return planned_kind


@dataclass(frozen=True)
class InterviewGenContract:
    schema: str
    rules: str
    kind: str
    max_options: int = 0
    max_tests: int = 0
    policy_version: str = SESSION_VERSION


def plan_interview_gen_contract(kind: str) -> InterviewGenContract:
    """JSON schema + writer rules for the next interview question kind."""
    k = (kind or "").strip().lower()
    if k == "mcq":
        return InterviewGenContract(
            schema=(
                '{"question":"...","options":["a","b","c","d"],"correct_index":0,'
                '"explanation":"why the correct option is right"}'
            ),
            rules="Exactly 4 options, exactly one correct. correct_index is 0-3.",
            kind="mcq",
            max_options=INTERVIEW_MCQ_MAX_OPTIONS,
        )
    if k == "coding":
        return InterviewGenContract(
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
        )
    return InterviewGenContract(
        schema='{"question":"..."}',
        rules="An open-ended question the candidate answers by typing. No options.",
        kind="typed",
    )


def pick_interview_coding_fallback(
    asked_count: int,
    bank: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Rotate the built-in coding bank when the LLM produced no tests."""
    if not bank:
        return {}
    idx = int(asked_count) % len(bank)
    return dict(bank[idx])


def evaluate_document_complete(
    *,
    page_complete: bool,
    on_last_page: bool,
    newspaper: bool,
    generation_pending: bool,
) -> bool:
    """Whole study range is done; newspaper waits for in-flight cook."""
    if not page_complete or not on_last_page:
        return False
    if newspaper and generation_pending:
        return False
    return True


def evaluate_page_prep_ready(
    *,
    has_coverage: bool,
    non_content: bool,
    budget: int,
    generated: int,
    coverage_complete: bool,
) -> bool:
    """Background-prep page is cooked enough to count as ready."""
    if not has_coverage:
        return False
    if non_content or int(budget) <= 0:
        return True
    return int(generated) >= int(budget) or coverage_complete


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
    if page.has_active_generate or not page.has_coverage or page.non_content:
        return CookScheduleVerdict(action="idle", page=page.page, reason="skip_page")
    if phase == "learn":
        if int(page.learn_budget) <= 0:
            return CookScheduleVerdict(action="idle", page=page.page, reason="no_learn_budget")
        if not page.coverage_complete or int(page.learn_generated) < int(page.learn_budget):
            return CookScheduleVerdict(
                action="cook",
                page=page.page,
                cook_mode="learn",
                reason="edition_learn",
            )
        return CookScheduleVerdict(action="idle", page=page.page, reason="learn_met")
    if int(page.learn_budget) > 0 and int(page.learn_generated) < int(page.learn_budget):
        return CookScheduleVerdict(action="idle", page=page.page, reason="learn_first")
    if int(page.test_budget) <= 0:
        return CookScheduleVerdict(action="idle", page=page.page, reason="no_test_budget")
    if int(page.test_generated) < int(page.test_budget):
        return CookScheduleVerdict(
            action="cook",
            page=page.page,
            cook_mode="test",
            reason="edition_test",
        )
    return CookScheduleVerdict(action="idle", page=page.page, reason="test_met")


def plan_newspaper_cook_target(
    pages: Sequence[NewspaperPageCookSignal],
) -> CookScheduleVerdict:
    """Learn cook across pages first, then test cook."""
    for page in pages:
        verdict = evaluate_newspaper_page_cook(page, phase="learn")
        if verdict.action == "cook":
            return verdict
    for page in pages:
        verdict = evaluate_newspaper_page_cook(page, phase="test")
        if verdict.action == "cook":
            return verdict
    return CookScheduleVerdict(action="idle", reason="edition_cook_idle")


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
    if int(saved) != 0:
        return False
    if int(start_sequence) + int(batch_size) < int(budget):
        return False
    if has_targets:
        return False
    return bool(has_aspects)


@dataclass(frozen=True)
class AuxCookSpawnVerdict:
    spawn_coding: bool
    spawn_debug: bool
    policy_version: str = SESSION_VERSION


def alias_debuggable(*, programmable: bool, debuggable: bool | None) -> bool:
    """Debug cook follows an explicit flag, else programmable pages."""
    if debuggable is None:
        return bool(programmable)
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
    if n <= 0:
        return 0
    return min(max(1, int(first_batch)), n)


def evaluate_background_first_batch(
    *,
    budget: int,
    generated: int,
    first_batch: int = 1,
) -> int:
    """Kick the first cook after background triage, or 0 to skip."""
    if int(generated) > 0:
        return 0
    return plan_first_cook_batch(budget=budget, first_batch=first_batch)


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
    if not has_next:
        return TransitionNextPlan(False, False)
    if not next_has_coverage:
        return TransitionNextPlan(True, False)
    allow = bool(generate_next_page) and int(current_budget) > 0
    cook = allow and int(next_generated) == 0
    return TransitionNextPlan(False, cook)


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
    if from_page not in study:
        return ()
    start = study.index(from_page) + 1
    window = study[start : start + max(0, int(lookahead))]
    covered = set(covered_pages)
    active = set(active_triage_pages)
    return tuple(p for p in window if p not in covered and p not in active)


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
    if not has_coverage:
        return PageAdvancePlan("triage")
    if int(generated) <= 0:
        return PageAdvancePlan("cook_first_batch")
    if not rag_ready:
        return PageAdvancePlan("ingest_rag")
    return PageAdvancePlan("noop")


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
    if not missing:
        return ()
    cap = max(1, int(batch_size))
    return tuple(int(p) for p in list(missing)[:cap])


def plan_newspaper_recovery_batch() -> int:
    """How many stuck ready-doc editions one uncooked recovery tick may re-enqueue."""
    return NEWSPAPER_RECOVERY_EDITION_LIMIT


def plan_newspaper_edition_questions_limit(requested: int | None = None) -> int:
    """How many edition MCQs newspaper practice may list (default + hard max)."""
    n = (
        NEWSPAPER_EDITION_QUESTIONS_DEFAULT
        if requested is None
        else int(requested)
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
    if newspaper:
        return NewspaperServeScope(True, True, True, True)
    return NewspaperServeScope(False, False, False, False)


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
    if status in ("indexing", "pending"):
        return "reingest"
    if status == "ready":
        return "recook"
    return "idle"


def plan_newspaper_uncooked_followup(*, cook_enqueued: bool) -> NewspaperUncookedFollowup:
    """After a ready-doc recook attempt: wait for the job or promote the catalog."""
    return "wait" if cook_enqueued else "promote_ready"


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
    if int(saved) <= 0 and bool(has_targets):
        return GenerationBatchOutcome(
            "failed",
            "No questions passed quality gates",
            "generation_empty",
        )
    return GenerationBatchOutcome("succeeded", None, None)


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
    qi = int(q_in_round) + 1
    ri = int(round_index)
    if qi >= int(round_question_count):
        ri, qi = ri + 1, 0
    if ri >= int(round_count):
        return InterviewAdvancePlan(True, int(round_count), 0)
    return InterviewAdvancePlan(False, ri, qi)


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
    if int(counted_pages) <= 1 or not selected_range:
        return StudyRangeHealPlan(False, False)
    pages = selected_range.get("pages")
    only_page_one = (isinstance(pages, list) and pages == [1]) or (
        int(selected_range.get("from") or 0) == 1
        and int(selected_range.get("to") or 0) == 1
        and not pages
    )
    if not only_page_one:
        return StudyRangeHealPlan(False, False)
    reset = (doc_status or "").strip().lower() in {"ready", "indexing"}
    return StudyRangeHealPlan(True, reset)


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
    if kind == "flashcards":
        return AuxiliaryArtifactCap("flashcards", 0, FLASHCARD_MAX, FLASHCARD_MAX)
    if kind == "topics":
        return AuxiliaryArtifactCap("topics", 0, TOPIC_OUTLINE_MAX, TOPIC_OUTLINE_MAX)
    if kind == "memory_palace":
        return AuxiliaryArtifactCap(
            "memory_palace",
            MEMORY_PALACE_MIN_STATIONS,
            MEMORY_PALACE_MAX_STATIONS,
            MEMORY_PALACE_MAX_STATIONS,
        )
    return AuxiliaryArtifactCap("quiz", 1, QUIZ_MAX_QUESTIONS, QUIZ_DEFAULT_COUNT)


def clamp_auxiliary_count(kind: AuxiliaryKind, requested: int | None) -> int:
    """Clamp a requested artifact count into the session cap."""
    plan = plan_auxiliary_artifact_cap(kind)
    n = int(requested) if requested else plan.default_count
    return max(plan.min_count, min(n, plan.max_count))


@dataclass(frozen=True)
class AuxiliaryOutputVerdict:
    keep: bool
    reason: str
    policy_version: str = SESSION_VERSION


def evaluate_auxiliary_output(kind: AuxiliaryKind, count: int) -> AuxiliaryOutputVerdict:
    """Keep or reject a generated auxiliary artifact after count is known."""
    cap = plan_auxiliary_artifact_cap(kind)
    if int(count) < cap.min_count:
        return AuxiliaryOutputVerdict(False, "below_min")
    return AuxiliaryOutputVerdict(True, "ok")


AuxiliaryMapKind = Literal[
    "flashcards", "topics", "memory_palace", "notes", "summarize", "audiobook"
]
AUXILIARY_MAP_CONCURRENCY = 6
AUDIOBOOK_MAP_CONCURRENCY = 4
AUXILIARY_CHUNK_LOAD_LIMIT = 200


def plan_auxiliary_map_concurrency(kind: AuxiliaryMapKind) -> int:
    """How many chunk-map LLM calls may run in parallel for one artifact cook."""
    if kind == "audiobook":
        return AUDIOBOOK_MAP_CONCURRENCY
    return AUXILIARY_MAP_CONCURRENCY


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
    if kind == "saved_notes":
        return SAVED_NOTES_MAX
    return BRAINSTORM_IDEAS_MAX


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
    if kind == "flashcards":
        return AuxiliaryFieldCaps("flashcards", {"front": 400, "back": 600})
    if kind == "topics":
        return AuxiliaryFieldCaps("topics", {"title": 120, "summary": 300})
    if kind == "memory_palace":
        return AuxiliaryFieldCaps(
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
        )
    return AuxiliaryFieldCaps(
        "quiz",
        {
            "prompt": 600,
            "explanation": 600,
            "answer": 1200,
            "options": 8,
            "pairs": 8,
        },
    )


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
    cap = (
        SUMMARIZE_SINGLE_SHOT_MAX_TOKENS
        if single_shot_max_tokens is None
        else int(single_shot_max_tokens)
    )
    if int(token_count) <= cap:
        return AuxiliaryGenerationPlan("single_shot", cap)
    return AuxiliaryGenerationPlan("map_reduce", cap)


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
