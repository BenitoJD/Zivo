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

from app.engine_runtime import pick
from app.services.content_worthiness import evaluate_newspaper_cook_gate, evaluate_newspaper_structure

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
    def _judge() -> dict | None:
        from app.services.newspaper_relevance import judge_newspaper_relevance

        return judge_newspaper_relevance(db, page_text)

    def _maybe_judge() -> dict | None:
        structure = evaluate_newspaper_structure(page_text)
        return pick(structure.label == "editorial", _judge, lambda: None)

    judged = pick(db is not None, _maybe_judge, lambda: None)
    verdict = evaluate_newspaper_cook_gate(page_text, relevance=judged)
    return verdict.label, verdict.rationale


def should_cook_newspaper_page(db: Session | None, page_text: str) -> bool:
    """True iff the page passes both the deterministic ad gate and the LLM
    exam-relevance judgment. Pass db=None to skip relevance (permissive)."""
    verdict, _ = newspaper_page_verdict(db, page_text)
    return verdict == "cook"
