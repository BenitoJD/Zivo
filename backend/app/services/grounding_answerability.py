"""Grounding / Answerability Engine — evidence-local answerability.

Design: docs/GROUNDING_ANSWERABILITY_ENGINE.md
Version: qb.ground.v1
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

GROUND_VERSION = "qb.ground.v1"

# Default overlap for advisory / general evaluate_grounding.
DEFAULT_MIN_SCORE = 0.08
# Cook path: looser overlap but only fatal when the page has enough words to judge.
COOK_MIN_SCORE = 0.02
COOK_MIN_PAGE_WORDS = 20

_TOKEN = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True)
class GroundingVerdict:
    grounded: bool
    score: float
    flaw_codes: tuple[str, ...]
    policy_version: str = GROUND_VERSION
    fatal: bool = False
    details: str = ""


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def evaluate_grounding(
    *,
    stem: str,
    correct_texts: Sequence[str],
    page_text: str,
    verify_flaw_code: str | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> GroundingVerdict:
    if verify_flaw_code in ("not_grounded", "wrong_answer_key"):
        return GroundingVerdict(False, 0.0, (verify_flaw_code,), fatal=True)
    page = _tokens(page_text)
    if not page:
        return GroundingVerdict(False, 0.0, ("not_grounded",), fatal=True)
    probe = _tokens(stem) | set().union(*(_tokens(t) for t in correct_texts))
    if not probe:
        return GroundingVerdict(False, 0.0, ("not_grounded",), fatal=True)
    overlap = len(probe & page) / max(len(probe), 1)
    grounded = overlap >= min_score
    codes = () if grounded else ("not_grounded",)
    return GroundingVerdict(grounded, overlap, codes, fatal=not grounded)


def evaluate_grounding_for_cook(
    *,
    stem: str,
    correct_texts: Sequence[str],
    page_text: str,
    verify_flaw_code: str | None = None,
) -> GroundingVerdict:
    """Cook-path grounding: owns min_score + page-density fatality policy.

    Thin excerpts stay advisory (fatal=False) so cook yield / rewrite loops stay
    stable; only pages with enough words get a fatal ``not_grounded``.
    """
    base = evaluate_grounding(
        stem=stem,
        correct_texts=correct_texts,
        page_text=page_text,
        verify_flaw_code=verify_flaw_code,
        min_score=COOK_MIN_SCORE,
    )
    if verify_flaw_code in ("not_grounded", "wrong_answer_key"):
        return base
    page_words = len((page_text or "").split())
    if page_words < COOK_MIN_PAGE_WORDS:
        return GroundingVerdict(
            grounded=base.grounded,
            score=base.score,
            flaw_codes=base.flaw_codes if not base.grounded else (),
            fatal=False,
            details=f"thin_page_words={page_words}",
        )
    if not base.grounded and base.score < COOK_MIN_SCORE:
        return GroundingVerdict(
            grounded=False,
            score=base.score,
            flaw_codes=("not_grounded",),
            fatal=True,
            details=f"grounding_score={base.score:.3f}",
        )
    return GroundingVerdict(
        grounded=base.grounded,
        score=base.score,
        flaw_codes=base.flaw_codes,
        fatal=False,
        details="",
    )
