"""Drop ads / junk newspaper pages before MCQ generation.

Two stages, by design (ADR 0004 seam):

1. Deterministic ad / junk / masthead detection (here) — cheap, reliable, kept
   as heuristics. These are structural filters (classifieds, price/phone
   density, registration boilerplate), NOT topic allowlists.
2. Exam-relevance judgment — formerly a ~200-phrase hardcoded keyword allowlist
   (_THEME_KEYWORDS / _OFF_SYLLABUS_MARKERS) that failed on paraphrased exam
   content. Replaced by an LLM meaning-judgment in newspaper_relevance.py so a
   polity page about "protests / FIRs / Centre" is recognized even without the
   literal keywords "supreme court / fundamental rights".
"""

from __future__ import annotations

import re
from typing import Final

from sqlalchemy.orm import Session

NEWSPAPER_EXAM_CONTENT_TYPE: Final[str] = "newspaper_upsc"

# --- Ad / junk markers (deterministic, kept) --------------------------------

_AD_MARKERS = re.compile(
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

_MASTHEAD_MARKERS = re.compile(
    r"\b(volume\s+\d+|regd\.?\s*no\.?|postal\s+regn|rni\s+no)\b",
    re.I,
)


def classify_page_text(page_text: str) -> tuple[str, str]:
    """Return (label, rationale). label in editorial|ad|masthead|low_signal."""
    text = (page_text or "").strip()
    if len(text) < 120:
        return "low_signal", "Too little extractable text for study."
    ad_hits = len(_AD_MARKERS.findall(text))
    if ad_hits >= 2 or (ad_hits >= 1 and len(text) < 800):
        return "ad", f"Ad/classified markers ({ad_hits})."
    if _MASTHEAD_MARKERS.search(text) and len(text) < 600:
        return "masthead", "Looks like masthead / registration boilerplate."
    # Dense price/phone patterns without prose → ad-ish
    phones = len(re.findall(r"\b\d{5,}[-/\s]?\d{4,}\b", text))
    if phones >= 4 and len(text) < 1500:
        return "ad", "Many phone/price-like tokens; treat as ad page."
    # Property / classified density
    prop = len(re.findall(r"\b(?:₹|rs\.?)\s*\d", text, re.I))
    if prop >= 6 and len(text) < 2000:
        return "ad", "Dense price tokens; treat as classified/ad page."
    return "editorial", "Usable editorial signal."


def is_editorial(page_text: str) -> bool:
    label, _ = classify_page_text(page_text)
    return label == "editorial"


def newspaper_page_verdict(db: Session | None, page_text: str) -> tuple[str, str]:
    """Combined gate for newspaper MCQ cooking.

    Returns (verdict, rationale) where verdict is one of:
    cook | ad | masthead | low_signal | off_syllabus

    Ad / masthead / low-signal are deterministic (``classify_page_text``). The
    exam-relevance verdict is an LLM judgment via ``judge_newspaper_relevance``;
    when ``db`` is None (no LLM available), relevance is skipped permissively so
    the page proceeds to ``cook`` and downstream gates still filter junk.
    """
    label, rationale = classify_page_text(page_text)
    if label != "editorial":
        return label, rationale

    # Exam relevance is now a meaning judgment, not a keyword allowlist.
    if db is None:
        return "cook", "Editorial; relevance judge unavailable, allowing."
    from app.services.newspaper_relevance import judge_newspaper_relevance

    judged = judge_newspaper_relevance(db, page_text)
    if not judged.get("relevant"):
        return "off_syllabus", str(judged.get("rationale") or "Not exam-relevant.")
    theme = judged.get("theme") or ""
    theme_bit = f" theme={theme}" if theme else ""
    why = str(judged.get("rationale") or "Exam-relevant.").strip()
    return "cook", f"Editorial + exam-relevant.{theme_bit} {why}".strip()


def should_cook_newspaper_page(db: Session | None, page_text: str) -> bool:
    """True iff the page passes both the deterministic ad gate and the LLM
    exam-relevance judgment. Pass db=None to skip relevance (permissive)."""
    verdict, _ = newspaper_page_verdict(db, page_text)
    return verdict == "cook"
