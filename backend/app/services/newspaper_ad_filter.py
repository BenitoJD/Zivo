"""Drop ads / junk newspaper pages before MCQ generation.

Two stages, by design (ADR 0004 seam):

1. Deterministic ad / junk / masthead detection — Content Worthiness
   ``evaluate_newspaper_structure`` (this module is the newspaper cook wrapper).
2. Exam-relevance judgment — formerly a ~200-phrase hardcoded keyword allowlist
   (_THEME_KEYWORDS / _OFF_SYLLABUS_MARKERS) that failed on paraphrased exam
   content. Replaced by an LLM meaning-judgment in newspaper_relevance.py so a
   polity page about "protests / FIRs / Centre" is recognized even without the
   literal keywords "supreme court / fundamental rights".
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.orm import Session

from app.services.content_worthiness import evaluate_newspaper_structure

NEWSPAPER_EXAM_CONTENT_TYPE: Final[str] = "newspaper_upsc"


def classify_page_text(page_text: str) -> tuple[str, str]:
    """Return (label, rationale). label in editorial|ad|masthead|low_signal."""
    verdict = evaluate_newspaper_structure(page_text)
    return verdict.label, verdict.rationale


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
