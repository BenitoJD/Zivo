"""Content Worthiness Gate Engine — skip non-content / empty / ad / junk pages.

Design: docs/CONTENT_WORTHINESS_ENGINE.md
Version: qb.worth.v1

Callers depend on ``evaluate_worthiness`` (and ``evaluate_vision_glance`` for
empty-page multimodal glances). Newspaper syllabus heuristics and junk-letter
gates live behind this facade (ADR 0004 / holy grail).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

WORTH_VERSION = "qb.worth.v1"
DEFAULT_WORTH_POLICY = "worth_v1"

# Consecutive blank/empty pages before prompting the learner to reselect pages.
import os as _os

EMPTY_PAGE_RESELECT_STREAK = int(_os.getenv("ZIVO_EMPTY_PAGE_RESELECT_STREAK", "5"))

WorthReason = Literal[
    "ok",
    "non_content",
    "empty",
    "empty_vision_usable",
    "empty_vision_blank",
    "ad_likely",
    "ad_copy",
    "too_short",
    "junk_text",
    "newspaper_ad",
    "newspaper_masthead",
    "newspaper_low_signal",
    "newspaper_off_syllabus",
]

_AD_HINT = re.compile(
    r"\b(subscribe now|limited time offer|click here|sponsored content|advertisement)\b",
    re.I,
)

_POLICY_ALIASES = {
    "worth": "worth_v1",
    "default": "worth_v1",
}

_NEWSPAPER_AD_MARKERS = re.compile(
    r"\b("
    r"advertisement|classifieds?|matrimonial|tenders?|"
    r"subscribe\s+now|scan\s+qr|whatsapp\s+us|"
    r"limited\s+period\s+offer|buy\s+now|call\s+toll\s*free|"
    r"horoscope|crossword|sudoku|weather\s+report|"
    r"flat\s+for\s+sale|sq\.?\s*ft|emi\s+starts|walk[- ]in\s+interview|"
    r"job\s+vacanc(?:y|ies)|situations?\s+vacant|"
    r"showtimes?|box\s+office|now\s+showing|"
    r"lottery|jackpot|astrology|"
    r"buy\s+call|sell\s+call|target\s+price|"
    r"prime\s+time|tv\s+guide|channel\s+listing"
    r")\b",
    re.I,
)

_NEWSPAPER_MASTHEAD_MARKERS = re.compile(
    r"\b(volume\s+\d+|regd\.?\s*no\.?|postal\s+regn|rni\s+no)\b",
    re.I,
)

NewspaperStructureLabel = Literal["editorial", "ad", "masthead", "low_signal"]


@dataclass(frozen=True)
class WorthinessVerdict:
    worthy: bool
    reason: str
    policy: str = DEFAULT_WORTH_POLICY
    policy_version: str = WORTH_VERSION
    details: str = ""


@dataclass(frozen=True)
class NewspaperStructureVerdict:
    label: NewspaperStructureLabel
    rationale: str
    policy: str = DEFAULT_WORTH_POLICY
    policy_version: str = WORTH_VERSION


def evaluate_newspaper_structure(page_text: str, *, policy: str | None = None) -> NewspaperStructureVerdict:
    """Deterministic ad / masthead / low-signal gate before LLM relevance."""
    pol = normalize_policy(policy)
    text = (page_text or "").strip()
    if len(text) < 120:
        return NewspaperStructureVerdict("low_signal", "Too little extractable text for study.", policy=pol)
    ad_hits = len(_NEWSPAPER_AD_MARKERS.findall(text))
    if ad_hits >= 2 or (ad_hits >= 1 and len(text) < 800):
        return NewspaperStructureVerdict("ad", f"Ad/classified markers ({ad_hits}).", policy=pol)
    if _NEWSPAPER_MASTHEAD_MARKERS.search(text) and len(text) < 600:
        return NewspaperStructureVerdict("masthead", "Looks like masthead / registration boilerplate.", policy=pol)
    phones = len(re.findall(r"\b\d{5,}[-/\s]?\d{4,}\b", text))
    if phones >= 4 and len(text) < 1500:
        return NewspaperStructureVerdict("ad", "Many phone/price-like tokens; treat as ad page.", policy=pol)
    prop = len(re.findall(r"\b(?:₹|rs\.?)\s*\d", text, re.I))
    if prop >= 6 and len(text) < 2000:
        return NewspaperStructureVerdict("ad", "Dense price tokens; treat as classified/ad page.", policy=pol)
    return NewspaperStructureVerdict("editorial", "Usable editorial signal.", policy=pol)


NewspaperCookLabel = Literal["cook", "ad", "masthead", "low_signal", "off_syllabus"]


@dataclass(frozen=True)
class NewspaperCookVerdict:
    label: NewspaperCookLabel
    rationale: str
    policy: str = DEFAULT_WORTH_POLICY
    policy_version: str = WORTH_VERSION


def evaluate_newspaper_cook_gate(
    page_text: str,
    *,
    relevance: dict | None,
    policy: str | None = None,
) -> NewspaperCookVerdict:
    """Structure + exam-relevance cook/skip. LLM judge stays plumbing."""
    structure = evaluate_newspaper_structure(page_text, policy=policy)
    pol = structure.policy
    if structure.label == "ad":
        return NewspaperCookVerdict("ad", structure.rationale, policy=pol)
    if structure.label == "masthead":
        return NewspaperCookVerdict("masthead", structure.rationale, policy=pol)
    if structure.label == "low_signal":
        return NewspaperCookVerdict("low_signal", structure.rationale, policy=pol)
    if relevance is None:
        return NewspaperCookVerdict(
            "cook",
            "Editorial; relevance judge unavailable, allowing.",
            policy=pol,
        )
    if not relevance.get("relevant"):
        why = str(relevance.get("rationale") or "Not exam-relevant.")
        return NewspaperCookVerdict("off_syllabus", why, policy=pol)
    theme = relevance.get("theme") or ""
    theme_bit = f" theme={theme}" if theme else ""
    why = str(relevance.get("rationale") or "Exam-relevant.").strip()
    return NewspaperCookVerdict(
        "cook",
        f"Editorial + exam-relevant.{theme_bit} {why}".strip(),
        policy=pol,
    )


def evaluate_empty_study_reason(
    *,
    document_complete: bool,
    newspaper: bool,
    questions_generated: int,
    questions_answered: int,
    generation_pending: bool,
    range_has_no_questions: bool,
) -> str | None:
    """When the UI should say the study range has nothing to ask."""
    if not range_has_no_questions:
        return None
    empty_pool = int(questions_generated) == 0 and int(questions_answered) == 0
    if document_complete and empty_pool:
        return "no_testable_content"
    if newspaper and int(questions_generated) == 0 and not generation_pending:
        return "no_testable_content"
    return None


@dataclass(frozen=True)
class LlmTriageUnitsVerdict:
    zero_question: bool
    reason: str
    policy_version: str = WORTH_VERSION


def evaluate_llm_triage_units(
    *,
    content_type: str | None,
    usable: bool | None,
    aspect_count: int,
) -> LlmTriageUnitsVerdict:
    """Honest zero when the model judged non-content, unusable, or no units."""
    if (content_type or "") == "non_content":
        return LlmTriageUnitsVerdict(True, "non_content")
    if usable is False:
        return LlmTriageUnitsVerdict(True, "unusable")
    if int(aspect_count) <= 0:
        return LlmTriageUnitsVerdict(True, "no_aspects")
    return LlmTriageUnitsVerdict(False, "ok")


def evaluate_digest_page_worthy(
    *,
    mcq_count: int,
    page_text: str,
    db: "Session | None" = None,
) -> bool:
    """Cooked MCQ pages are digest-worthy; otherwise run the newspaper gate."""
    if int(mcq_count) > 0:
        return True
    return evaluate_worthiness(page_text=page_text, newspaper=True, db=db).worthy


@dataclass(frozen=True)
class NewspaperPageSkipPlan:
    skip: bool
    reason: str
    details: str = ""
    question_budget: int = 0
    non_content: bool = True
    mark_complete: bool = True
    policy_version: str = WORTH_VERSION


def plan_newspaper_batch_gate(
    *,
    page_text: str,
    db: "Session | None" = None,
) -> NewspaperPageSkipPlan:
    """Cook skip plan when newspaper worthiness fails mid-batch."""
    worth = evaluate_worthiness(page_text=page_text, newspaper=True, db=db)
    if worth.worthy:
        return NewspaperPageSkipPlan(
            skip=False,
            reason="ok",
            details="",
            question_budget=0,
            non_content=False,
            mark_complete=False,
        )
    return NewspaperPageSkipPlan(
        skip=True,
        reason=worth.reason,
        details=str(worth.details or worth.reason),
    )


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_WORTH_POLICY).strip().lower()
    return _POLICY_ALIASES.get(p, p or DEFAULT_WORTH_POLICY)


def looks_like_junk(text: str | None, *, min_chars: int = 24) -> bool:
    """Cheap coherent-language gate (ex-_looks_like_junk). Owned by this engine."""
    t = (text or "").strip()
    if len(t) < min_chars:
        return True
    letters = sum(1 for c in t if c.isalpha())
    if letters / max(len(t), 1) < 0.45:
        return True
    words = re.findall(r"[^\W\d_]{2,}", t, re.UNICODE)
    return len(words) < 8


def evaluate_worthiness(
    *,
    page_text: str | None = None,
    non_content: bool = False,
    empty: bool = False,
    ad_likely: bool = False,
    min_chars: int = 40,
    newspaper: bool = False,
    check_junk: bool = False,
    policy: str | None = None,
    db: Session | None = None,
) -> WorthinessVerdict:
    """Sole worthiness seam. Orchestration must not invent parallel skip ladders.

    ``db`` flows to the newspaper LLM exam-relevance judge. When None (e.g. a
    quick heuristic check or a unit test), relevance is skipped permissively —
    only the deterministic ad / length gates apply, and a newspaper page that
    clears them is treated as worthy.
    """
    pol = normalize_policy(policy)
    if non_content:
        return WorthinessVerdict(False, "non_content", policy=pol)
    if empty:
        return WorthinessVerdict(False, "empty", policy=pol)
    if ad_likely:
        return WorthinessVerdict(False, "ad_likely", policy=pol)

    text = (page_text or "").strip()

    if newspaper:
        from app.services.newspaper_ad_filter import newspaper_page_verdict

        verdict, rationale = newspaper_page_verdict(db, text)
        if verdict != "cook":
            reason_map = {
                "ad": "newspaper_ad",
                "masthead": "newspaper_masthead",
                "low_signal": "newspaper_low_signal",
                "off_syllabus": "newspaper_off_syllabus",
            }
            return WorthinessVerdict(
                False,
                reason_map.get(verdict, "newspaper_off_syllabus"),
                policy=pol,
                details=rationale,
            )
        # Editorial + exam-relevant: still apply generic length/ad checks below.

    if len(text) < min_chars:
        return WorthinessVerdict(False, "too_short", policy=pol)
    if _AD_HINT.search(text):
        return WorthinessVerdict(False, "ad_copy", policy=pol)
    if check_junk and looks_like_junk(text):
        return WorthinessVerdict(False, "junk_text", policy=pol)
    return WorthinessVerdict(True, "ok", policy=pol)


def evaluate_vision_glance(
    *,
    usable: bool,
    rationale: str = "",
    policy: str | None = None,
) -> WorthinessVerdict:
    """Map multimodal empty-page glance into a worthiness verdict.

    Vision LLM / cache / render stay in ``vision.judge_page_has_content``;
    this seam owns whether usable vision content changes the empty-page reason.
    Empty extractable text still cannot cook MCQs (worthy=False).
    """
    pol = normalize_policy(policy)
    detail = (rationale or "").strip()
    if usable:
        return WorthinessVerdict(
            False,
            "empty_vision_usable",
            policy=pol,
            details=detail or "Page looks like it has content, but no extractable text.",
        )
    return WorthinessVerdict(
        False,
        "empty_vision_blank",
        policy=pol,
        details=detail or "Vision glance: no useful study content.",
    )


@dataclass(frozen=True)
class EmptyPageReselectVerdict:
    prompt_reselect: bool
    streak: int
    reason: str
    policy: str = DEFAULT_WORTH_POLICY
    policy_version: str = WORTH_VERSION


def plan_empty_page_reselect(
    *,
    prior_streak: int,
    vision_usable: bool,
    already_prompted: bool = False,
    prior_reason: str | None = None,
    streak_threshold: int = EMPTY_PAGE_RESELECT_STREAK,
    policy: str | None = None,
) -> EmptyPageReselectVerdict:
    """Whether blank-page streak / vision-usable should prompt page reselect."""
    pol = normalize_policy(policy)
    streak = max(0, int(prior_streak)) + 1
    if vision_usable:
        return EmptyPageReselectVerdict(
            prompt_reselect=True,
            streak=streak,
            reason="unreadable_content",
            policy=pol,
        )
    if streak >= max(1, int(streak_threshold)) or already_prompted:
        return EmptyPageReselectVerdict(
            prompt_reselect=True,
            streak=streak,
            reason=(prior_reason or "empty_pages_streak"),
            policy=pol,
        )
    return EmptyPageReselectVerdict(
        prompt_reselect=False,
        streak=streak,
        reason="",
        policy=pol,
    )
