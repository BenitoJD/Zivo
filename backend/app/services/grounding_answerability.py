"""Grounding / Answerability Engine — evidence-local answerability.

Design: docs/GROUNDING_ANSWERABILITY_ENGINE.md
Version: qb.ground.v1
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.engine_runtime import Pred, Rule, apply, first_match

GROUND_VERSION = "qb.ground.v1"

DEFAULT_MIN_SCORE = 0.08
COOK_MIN_SCORE = 0.02
COOK_MIN_PAGE_WORDS = 20

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_VERIFY_FATAL = {"not_grounded", "wrong_answer_key"}


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


_OVERLAP_EMPTY_RULES = (
    Rule(when=(Pred("empty", "truthy"),), action="empty"),
    Rule(when=(), action="overlap"),
)
_GROUNDED_CODE_RULES = (
    Rule(when=(Pred("grounded", "truthy"),), action="ok"),
    Rule(when=(), action="not_grounded"),
)
_VERIFY_FATAL_RULES = (
    Rule(when=(Pred("verify_fatal", "truthy"),), action="fatal"),
    Rule(when=(), action="overlap"),
)


def _overlap_grounding(
    *,
    stem: str,
    correct_texts: Sequence[str],
    page_text: str,
    min_score: float,
) -> GroundingVerdict:
    page = _tokens(page_text)
    probe = _tokens(stem) | set().union(*(_tokens(t) for t in correct_texts))

    def with_overlap() -> GroundingVerdict:
        overlap = len(probe & page) / max(len(probe), 1)
        grounded = overlap >= min_score
        codes = apply(
            first_match(_GROUNDED_CODE_RULES, {"grounded": grounded}).action,
            {"ok": lambda: (), "not_grounded": lambda: ("not_grounded",)},
        )
        return GroundingVerdict(grounded, overlap, codes, fatal=not grounded)

    return apply(
        first_match(_OVERLAP_EMPTY_RULES, {"empty": not page or not probe}).action,
        {
            "empty": lambda: GroundingVerdict(False, 0.0, ("not_grounded",), fatal=True),
            "overlap": with_overlap,
        },
    )


def evaluate_grounding(
    *,
    stem: str,
    correct_texts: Sequence[str],
    page_text: str,
    verify_flaw_code: str | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> GroundingVerdict:
    return apply(
        first_match(
            _VERIFY_FATAL_RULES,
            {"verify_fatal": verify_flaw_code in _VERIFY_FATAL},
        ).action,
        {
            "fatal": lambda: GroundingVerdict(
                False, 0.0, (str(verify_flaw_code),), fatal=True
            ),
            "overlap": lambda: _overlap_grounding(
                stem=stem,
                correct_texts=correct_texts,
                page_text=page_text,
                min_score=min_score,
            ),
        },
    )


_COOK_DENSITY_RULES = (
    Rule(when=(Pred("thin", "truthy"),), action="thin"),
    Rule(when=(Pred("fatal_low", "truthy"),), action="fatal"),
    Rule(when=(), action="keep"),
)

_COOK_VERIFY_RULES = (
    Rule(when=(Pred("verify_fatal", "truthy"),), action="base"),
    Rule(when=(), action="density"),
)


def _cook_density(base: GroundingVerdict, page_text: str) -> GroundingVerdict:
    page_words = len((page_text or "").split())
    hit = first_match(
        _COOK_DENSITY_RULES,
        {
            "thin": page_words < COOK_MIN_PAGE_WORDS,
            "fatal_low": (not base.grounded) and base.score < COOK_MIN_SCORE,
        },
    )
    return apply(
        hit.action,
        {
            "thin": lambda: GroundingVerdict(
                grounded=base.grounded,
                score=base.score,
                flaw_codes=apply(
                    first_match(_GROUNDED_CODE_RULES, {"grounded": base.grounded}).action,
                    {"ok": lambda: (), "not_grounded": lambda: base.flaw_codes},
                ),
                fatal=False,
                details=f"thin_page_words={page_words}",
            ),
            "fatal": lambda: GroundingVerdict(
                grounded=False,
                score=base.score,
                flaw_codes=("not_grounded",),
                fatal=True,
                details=f"grounding_score={base.score:.3f}",
            ),
            "keep": lambda: GroundingVerdict(
                grounded=base.grounded,
                score=base.score,
                flaw_codes=base.flaw_codes,
                fatal=False,
                details="",
            ),
        },
    )


def evaluate_grounding_for_cook(
    *,
    stem: str,
    correct_texts: Sequence[str],
    page_text: str,
    verify_flaw_code: str | None = None,
) -> GroundingVerdict:
    """Cook-path grounding: owns min_score + page-density fatality policy."""
    base = evaluate_grounding(
        stem=stem,
        correct_texts=correct_texts,
        page_text=page_text,
        verify_flaw_code=verify_flaw_code,
        min_score=COOK_MIN_SCORE,
    )
    return apply(
        first_match(
            _COOK_VERIFY_RULES,
            {"verify_fatal": verify_flaw_code in _VERIFY_FATAL},
        ).action,
        {
            "base": lambda: base,
            "density": lambda: _cook_density(base, page_text),
        },
    )
