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
from typing import Literal

from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick

WORTH_VERSION = "qb.worth.v1"
DEFAULT_WORTH_POLICY = "worth_v1"

# Consecutive blank/empty pages before prompting the learner to reselect pages.
import os as _os

EMPTY_PAGE_RESELECT_STREAK = int(_os.getenv("ZIVO_EMPTY_PAGE_RESELECT_STREAK", "5"))
# Extractable-text floor: below this, treat the page as sparse (OCR / empty skip).
WORTH_MIN_CHARS = 40
# Junk-letter gate: shorter than this is not coherent study text.
JUNK_MIN_CHARS = 24
# YouTube / similar imports need more than a sparse PDF page.
IMPORT_TRANSCRIPT_MIN_CHARS = 80
# Tiny uploads are genuinely one page; skip a storage round-trip to re-count.
TINY_FILE_PAGE_TRUST_BYTES = 2500
# Soft-paginate imported / native-overflow study units.
STUDY_PAGE_CHARS = 3200
NATIVE_SOFT_SPLIT_CHARS = STUDY_PAGE_CHARS * 2
HEADING_BREAK_MIN_CHARS = 600
SECTION_TITLE_MAX_CHARS = 160
# Newspaper exam-relevance judge: gist, not the whole aggregated page.
NEWSPAPER_RELEVANCE_MAX_CHARS = 6000
# Vision OCR cache: successful transcriptions last a month; BLANK is trusted
# only for the default 24h TTL so a transient empty 200 cannot hide a page.
VISION_OCR_TTL_SECONDS = 30 * 24 * 60 * 60
OCR_BLANK_TRUST_TTL_SECONDS = 24 * 60 * 60

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

_NEWSPAPER_STRUCTURE_RULES = (
    Rule(
        when=(Pred("too_short", "truthy"),),
        action="low_signal",
        extras={"rationale": "Too little extractable text for study."},
    ),
    Rule(when=(Pred("ad_markers", "truthy"),), action="ad", extras={"ad_hits": True}),
    Rule(
        when=(Pred("masthead", "truthy"),),
        action="masthead",
        extras={"rationale": "Looks like masthead / registration boilerplate."},
    ),
    Rule(
        when=(Pred("phones", "truthy"),),
        action="ad",
        extras={"rationale": "Many phone/price-like tokens; treat as ad page."},
    ),
    Rule(
        when=(Pred("prices", "truthy"),),
        action="ad",
        extras={"rationale": "Dense price tokens; treat as classified/ad page."},
    ),
    Rule(
        when=(),
        action="editorial",
        extras={"rationale": "Usable editorial signal."},
    ),
)

_RELEVANCE_RULES = (
    Rule(when=(Pred("empty", "truthy"),), action="empty"),
    Rule(when=(Pred("judge_error", "truthy"),), action="judge_unavailable"),
    Rule(when=(Pred("no_parsed", "truthy"),), action="no_judgment"),
    Rule(when=(), action="judged"),
)

_WORTH_EARLY_RULES = (
    Rule(when=(Pred("non_content", "truthy"),), action="non_content"),
    Rule(when=(Pred("empty", "truthy"),), action="empty"),
    Rule(when=(Pred("ad_likely", "truthy"),), action="ad_likely"),
    Rule(when=(), action="continue"),
)

_GENERIC_WORTH_RULES = (
    Rule(when=(Pred("too_short", "truthy"),), action="too_short"),
    Rule(when=(Pred("ad_copy", "truthy"),), action="ad_copy"),
    Rule(when=(Pred("junk", "truthy"),), action="junk_text"),
    Rule(when=(), action="ok"),
)

_LLM_TRIAGE_RULES = (
    Rule(when=(Pred("non_content", "truthy"),), action="non_content"),
    Rule(when=(Pred("unusable", "truthy"),), action="unusable"),
    Rule(when=(Pred("no_aspects", "truthy"),), action="no_aspects"),
    Rule(when=(), action="ok"),
)

_PAGE_COUNT_RULES = (
    Rule(when=(Pred("multi", "truthy"),), action="multi_page", extras={"trust": True}),
    Rule(when=(Pred("tiny", "truthy"),), action="tiny_one_page", extras={"trust": True}),
    Rule(when=(), action="recount", extras={"trust": False}),
)

_RESELECT_RULES = (
    Rule(
        when=(Pred("vision_usable", "truthy"),),
        action="unreadable_content",
        extras={"prompt": True},
    ),
    Rule(when=(Pred("prompt_now", "truthy"),), action="streak", extras={"prompt": True}),
    Rule(when=(), action="", extras={"prompt": False}),
)

_NEWSPAPER_REASON = {
    "ad": "newspaper_ad",
    "masthead": "newspaper_masthead",
    "low_signal": "newspaper_low_signal",
    "off_syllabus": "newspaper_off_syllabus",
}


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
    ad_hits = len(_NEWSPAPER_AD_MARKERS.findall(text))
    phones = len(re.findall(r"\b\d{5,}[-/\s]?\d{4,}\b", text))
    prop = len(re.findall(r"\b(?:₹|rs\.?)\s*\d", text, re.I))
    hit = first_match(
        _NEWSPAPER_STRUCTURE_RULES,
        {
            "too_short": len(text) < 120,
            "ad_markers": ad_hits >= 2 or (ad_hits >= 1 and len(text) < 800),
            "masthead": bool(_NEWSPAPER_MASTHEAD_MARKERS.search(text)) and len(text) < 600,
            "phones": phones >= 4 and len(text) < 1500,
            "prices": prop >= 6 and len(text) < 2000,
        },
    )
    rationale = pick(
        "ad_hits" in hit.extras,
        lambda: f"Ad/classified markers ({ad_hits}).",
        lambda: str(hit.extras.get("rationale") or ""),
    )
    return NewspaperStructureVerdict(hit.action, rationale, policy=pol)  # type: ignore[arg-type]


NewspaperCookLabel = Literal["cook", "ad", "masthead", "low_signal", "off_syllabus"]
NewspaperRelevanceReason = Literal[
    "judged",
    "empty",
    "judge_unavailable",
    "no_judgment",
]


@dataclass(frozen=True)
class NewspaperCookVerdict:
    label: NewspaperCookLabel
    rationale: str
    policy: str = DEFAULT_WORTH_POLICY
    policy_version: str = WORTH_VERSION


def _cook_from_relevance(relevance: dict, pol: str) -> NewspaperCookVerdict:
    theme = relevance.get("theme") or ""
    theme_bit = choose(bool(theme), f" theme={theme}", "")
    why = str(relevance.get("rationale") or "Exam-relevant.").strip()
    return NewspaperCookVerdict(
        "cook",
        f"Editorial + exam-relevant.{theme_bit} {why}".strip(),
        policy=pol,
    )


def _editorial_cook(relevance: dict | None, pol: str) -> NewspaperCookVerdict:
    return pick(
        relevance is None,
        lambda: NewspaperCookVerdict(
            "cook",
            "Editorial; relevance judge unavailable, allowing.",
            policy=pol,
        ),
        lambda: pick(
            not relevance.get("relevant"),
            lambda: NewspaperCookVerdict(
                "off_syllabus",
                str(relevance.get("rationale") or "Not exam-relevant."),
                policy=pol,
            ),
            lambda: _cook_from_relevance(relevance, pol),
        ),
    )


def evaluate_newspaper_cook_gate(
    page_text: str,
    *,
    relevance: dict | None,
    policy: str | None = None,
) -> NewspaperCookVerdict:
    """Structure + exam-relevance cook/skip. LLM judge stays plumbing."""
    structure = evaluate_newspaper_structure(page_text, policy=policy)
    pol = structure.policy
    return pick(
        structure.label != "editorial",
        lambda: NewspaperCookVerdict(structure.label, structure.rationale, policy=pol),
        lambda: _editorial_cook(relevance, pol),
    )


@dataclass(frozen=True)
class NewspaperRelevanceVerdict:
    relevant: bool
    theme: str
    rationale: str
    reason: NewspaperRelevanceReason
    policy: str = DEFAULT_WORTH_POLICY
    policy_version: str = WORTH_VERSION


def evaluate_newspaper_relevance_outcome(
    *,
    page_text: str,
    parsed: dict | None = None,
    judge_error: bool = False,
) -> NewspaperRelevanceVerdict:
    """Fail-open exam-relevance: empty rejects; LLM outage / unparseable allows."""
    hit = first_match(
        _RELEVANCE_RULES,
        {
            "empty": not (page_text or "").strip(),
            "judge_error": judge_error,
            "no_parsed": parsed is None,
        },
    )
    return apply(
        hit.action,
        {
            "empty": lambda: NewspaperRelevanceVerdict(
                False, "", "Empty page text.", "empty"
            ),
            "judge_unavailable": lambda: NewspaperRelevanceVerdict(
                True,
                "",
                "Relevance judge unavailable; allowing.",
                "judge_unavailable",
            ),
            "no_judgment": lambda: NewspaperRelevanceVerdict(
                True,
                "",
                "Relevance judge returned no judgment; allowing.",
                "no_judgment",
            ),
            "judged": lambda: NewspaperRelevanceVerdict(
                bool(parsed.get("relevant")),
                str(parsed.get("theme") or "").strip(),
                str(parsed.get("rationale") or "").strip(),
                "judged",
            ),
        },
    )


def plan_newspaper_relevance_input(page_text: str | None) -> str:
    """Trim page text to the exam-relevance judge budget."""
    return (page_text or "").strip()[:NEWSPAPER_RELEVANCE_MAX_CHARS]


@dataclass(frozen=True)
class VisionOcrCachePlan:
    transcription_ttl_seconds: int
    blank_trust_ttl_seconds: int
    policy_version: str = WORTH_VERSION


def plan_vision_ocr_cache() -> VisionOcrCachePlan:
    """How long to keep vision OCR hits, and when a cached BLANK is trustworthy."""
    return VisionOcrCachePlan(VISION_OCR_TTL_SECONDS, OCR_BLANK_TRUST_TTL_SECONDS)


def should_revalidate_ocr_blank(
    *,
    hit: str | None,
    requested_ttl_seconds: int,
    blank_trust_ttl_seconds: int | None = None,
) -> bool:
    """True when a BLANK hit was found under a longer TTL than we trust."""
    trust = pick(
        blank_trust_ttl_seconds is None,
        lambda: OCR_BLANK_TRUST_TTL_SECONDS,
        lambda: int(blank_trust_ttl_seconds),
    )
    return hit == "BLANK" and int(requested_ttl_seconds) > trust


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
    empty_pool = int(questions_generated) == 0 and int(questions_answered) == 0
    return pick(
        not range_has_no_questions,
        lambda: None,
        lambda: pick(
            document_complete and empty_pool,
            lambda: "no_testable_content",
            lambda: pick(
                newspaper and int(questions_generated) == 0 and not generation_pending,
                lambda: "no_testable_content",
                lambda: None,
            ),
        ),
    )


def should_probe_empty_study_range(
    *,
    document_complete: bool,
    newspaper: bool,
    questions_generated: int,
    questions_answered: int,
    generation_pending: bool,
) -> bool:
    """Run the expensive range scan only when the pool looks empty."""
    empty_pool = int(questions_generated) == 0 and int(questions_answered) == 0
    return (document_complete and empty_pool) or (
        newspaper and int(questions_generated) == 0 and not generation_pending
    )


def is_sparse_page_text(text: str | None, *, min_chars: int = WORTH_MIN_CHARS) -> bool:
    """True when extractable text is too short to quiz without OCR or a skip."""
    return len((text or "").strip()) < int(min_chars)


def evaluate_reference_extract(extract: str) -> bool:
    """Wikipedia/web extract is long enough and not a disambiguation stub."""
    lowered = (extract or "").strip().lower()
    return (
        not is_sparse_page_text(extract)
        and "may refer to" not in lowered
        and "can refer to" not in lowered
    )


def is_import_extract_too_short(
    text: str | None,
    *,
    min_chars: int = IMPORT_TRANSCRIPT_MIN_CHARS,
) -> bool:
    """Reject a URL, YouTube, or plain-text import that has almost no study text."""
    return is_sparse_page_text(text, min_chars=min_chars)


def should_trust_stored_page_count(*, size_bytes: int, stored_pages: int | None) -> bool:
    """Tiny files with a stored count of 1 do not need a reparse."""
    return int(size_bytes or 0) < TINY_FILE_PAGE_TRUST_BYTES and stored_pages == 1


@dataclass(frozen=True)
class StoredPageCountTrust:
    trust: bool
    pages: int
    reason: str
    policy_version: str = WORTH_VERSION


def plan_stored_page_count_trust(
    *,
    size_bytes: int,
    stored_pages: int | None,
) -> StoredPageCountTrust:
    """When parse may skip a MinIO re-count and keep the stored page total."""
    hit = first_match(
        _PAGE_COUNT_RULES,
        {
            "multi": stored_pages is not None and int(stored_pages) > 1,
            "tiny": should_trust_stored_page_count(
                size_bytes=size_bytes, stored_pages=stored_pages
            ),
        },
    )
    pages = apply(
        hit.action,
        {
            "multi_page": lambda: int(stored_pages),
            "tiny_one_page": lambda: 1,
            "recount": lambda: int(stored_pages or 0),
        },
    )
    return StoredPageCountTrust(bool(hit.extras["trust"]), pages, hit.action)


@dataclass(frozen=True)
class StudyPageSplitPlan:
    chars_per_page: int
    native_split_chars: int
    heading_break_min_chars: int
    section_title_max_chars: int
    policy_version: str = WORTH_VERSION


def plan_study_page_split() -> StudyPageSplitPlan:
    """How large a study page may be, and when to split native units or headings."""
    return StudyPageSplitPlan(
        STUDY_PAGE_CHARS,
        NATIVE_SOFT_SPLIT_CHARS,
        HEADING_BREAK_MIN_CHARS,
        SECTION_TITLE_MAX_CHARS,
    )


def should_split_native_unit(text: str, *, max_chars: int | None = None) -> bool:
    """True when a native PDF/DOCX/PPTX unit is too large to keep as one page."""
    cap = pick(max_chars is None, lambda: NATIVE_SOFT_SPLIT_CHARS, lambda: int(max_chars))
    return len(text or "") > cap


def should_break_on_section_heading(*, current_len: int, looks_like_heading: bool) -> bool:
    """Start a new page at a TOC heading only after the current page has real text."""
    return bool(looks_like_heading) and int(current_len) >= HEADING_BREAK_MIN_CHARS


def is_section_title_line(text: str) -> bool:
    """Heading lines are short titles, not a body that happens to start with Part 1."""
    return len((text or "").strip()) <= SECTION_TITLE_MAX_CHARS


WorthProbeStage = Literal["empty_page", "pre_llm"]


@dataclass(frozen=True)
class WorthinessProbe:
    empty: bool
    check_junk: bool
    min_chars: int
    policy_version: str = WORTH_VERSION


def plan_worthiness_probe(
    stage: WorthProbeStage,
    *,
    has_text: bool = True,
) -> WorthinessProbe:
    """Thresholds for empty-page vision vs pre-LLM junk skip."""
    return pick(
        stage == "empty_page",
        lambda: WorthinessProbe(
            empty=True,
            check_junk=False,
            min_chars=WORTH_MIN_CHARS,
        ),
        lambda: WorthinessProbe(
            empty=not has_text,
            check_junk=True,
            min_chars=JUNK_MIN_CHARS,
        ),
    )


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
    hit = first_match(
        _LLM_TRIAGE_RULES,
        {
            "non_content": (content_type or "") == "non_content",
            "unusable": usable is False,
            "no_aspects": int(aspect_count) <= 0,
        },
    )
    return LlmTriageUnitsVerdict(hit.action != "ok", hit.action)


def should_heuristic_fallback_empty_aspects(
    *,
    allow_zero: bool,
    aspect_count: int,
) -> bool:
    """Legacy (honest-zero off): empty LLM aspects fall back to heuristic N."""
    return not allow_zero and int(aspect_count) <= 0


def evaluate_digest_page_worthy(
    *,
    mcq_count: int,
    page_text: str,
    db: Session | None = None,
) -> bool:
    """Cooked MCQ pages are digest-worthy; otherwise run the newspaper gate."""
    return pick(
        int(mcq_count) > 0,
        lambda: True,
        lambda: evaluate_worthiness(page_text=page_text, newspaper=True, db=db).worthy,
    )


@dataclass(frozen=True)
class NewspaperPageSkipPlan:
    action: str
    skip: bool
    reason: str
    details: str = ""
    question_budget: int = 0
    non_content: bool = True
    mark_complete: bool = True
    policy_version: str = WORTH_VERSION


_NEWSPAPER_BATCH_GATE_RULES = (
    Rule(when=(Pred("worthy", "truthy"),), action="cook"),
    Rule(when=(), action="skip"),
)


def plan_newspaper_batch_gate(
    *,
    page_text: str,
    db: Session | None = None,
) -> NewspaperPageSkipPlan:
    """Cook skip plan when newspaper worthiness fails mid-batch."""
    worth = evaluate_worthiness(page_text=page_text, newspaper=True, db=db)
    hit = first_match(_NEWSPAPER_BATCH_GATE_RULES, {"worthy": worth.worthy})
    return apply(
        hit.action,
        {
            "cook": lambda: NewspaperPageSkipPlan(
                action="cook",
                skip=False,
                reason="ok",
                details="",
                question_budget=0,
                non_content=False,
                mark_complete=False,
            ),
            "skip": lambda: NewspaperPageSkipPlan(
                action="skip",
                skip=True,
                reason=worth.reason,
                details=str(worth.details or worth.reason),
            ),
        },
    )


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_WORTH_POLICY).strip().lower()
    return _POLICY_ALIASES.get(p, p or DEFAULT_WORTH_POLICY)


def looks_like_junk(text: str | None, *, min_chars: int = JUNK_MIN_CHARS) -> bool:
    """Cheap coherent-language gate (ex-_looks_like_junk). Owned by this engine."""
    t = (text or "").strip()
    letters = sum(map(str.isalpha, t))
    words = re.findall(r"[^\W\d_]{2,}", t, re.UNICODE)
    return len(t) < min_chars or letters / max(len(t), 1) < 0.45 or len(words) < 8


def _generic_worth(
    text: str,
    min_chars: int,
    check_junk: bool,
    pol: str,
) -> WorthinessVerdict:
    hit = first_match(
        _GENERIC_WORTH_RULES,
        {
            "too_short": len(text) < min_chars,
            "ad_copy": bool(_AD_HINT.search(text)),
            "junk": check_junk and looks_like_junk(text),
        },
    )
    return WorthinessVerdict(hit.action == "ok", hit.action, policy=pol)


def _newspaper_skip_or_none(
    text: str,
    db: Session | None,
    pol: str,
) -> WorthinessVerdict | None:
    from app.services.newspaper_ad_filter import newspaper_page_verdict

    verdict, rationale = newspaper_page_verdict(db, text)
    return pick(
        verdict != "cook",
        lambda: WorthinessVerdict(
            False,
            _NEWSPAPER_REASON.get(verdict, "newspaper_off_syllabus"),
            policy=pol,
            details=rationale,
        ),
        lambda: None,
    )


def _worthiness_after_flags(
    page_text: str | None,
    newspaper: bool,
    min_chars: int,
    check_junk: bool,
    pol: str,
    db: Session | None,
) -> WorthinessVerdict:
    text = (page_text or "").strip()
    paper = pick(
        newspaper,
        lambda: _newspaper_skip_or_none(text, db, pol),
        lambda: None,
    )
    return pick(
        paper is not None,
        lambda: paper,
        lambda: _generic_worth(text, min_chars, check_junk, pol),
    )


def evaluate_worthiness(
    *,
    page_text: str | None = None,
    non_content: bool = False,
    empty: bool = False,
    ad_likely: bool = False,
    min_chars: int = WORTH_MIN_CHARS,
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
    hit = first_match(
        _WORTH_EARLY_RULES,
        {
            "non_content": non_content,
            "empty": empty,
            "ad_likely": ad_likely,
        },
    )
    return pick(
        hit.action != "continue",
        lambda: WorthinessVerdict(False, hit.action, policy=pol),
        lambda: _worthiness_after_flags(
            page_text, newspaper, min_chars, check_junk, pol, db
        ),
    )


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
    return pick(
        usable,
        lambda: WorthinessVerdict(
            False,
            "empty_vision_usable",
            policy=pol,
            details=detail or "Page looks like it has content, but no extractable text.",
        ),
        lambda: WorthinessVerdict(
            False,
            "empty_vision_blank",
            policy=pol,
            details=detail or "Vision glance: no useful study content.",
        ),
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
    hit = first_match(
        _RESELECT_RULES,
        {
            "vision_usable": vision_usable,
            "prompt_now": streak >= max(1, int(streak_threshold)) or already_prompted,
        },
    )
    reason = pick(
        hit.action == "streak",
        lambda: (prior_reason or "empty_pages_streak"),
        lambda: hit.action,
    )
    return EmptyPageReselectVerdict(
        prompt_reselect=bool(hit.extras["prompt"]),
        streak=streak,
        reason=reason,
        policy=pol,
    )


def should_skip_empty_page_vision(*, already_prompted: bool) -> bool:
    """Stop vision LLM spend after the learner was already asked to reselect."""
    return bool(already_prompted)
