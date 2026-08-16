"""Session Design Engine - soft serve-session length + pool schedule thresholds.

Design: docs/SESSION_DESIGN_ENGINE.md
Version: qb.session.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.question_budget import SESSION_SOFT

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
