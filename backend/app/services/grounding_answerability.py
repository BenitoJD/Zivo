"""Grounding / Answerability Engine — evidence-local answerability.

Design: docs/GROUNDING_ANSWERABILITY_ENGINE.md
Version: qb.ground.v1
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

GROUND_VERSION = "qb.ground.v1"

_TOKEN = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True)
class GroundingVerdict:
    grounded: bool
    score: float
    flaw_codes: tuple[str, ...]
    policy_version: str = GROUND_VERSION


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def evaluate_grounding(
    *,
    stem: str,
    correct_texts: Sequence[str],
    page_text: str,
    verify_flaw_code: str | None = None,
    min_score: float = 0.08,
) -> GroundingVerdict:
    if verify_flaw_code in ("not_grounded", "wrong_answer_key"):
        return GroundingVerdict(False, 0.0, (verify_flaw_code,))
    page = _tokens(page_text)
    if not page:
        return GroundingVerdict(False, 0.0, ("not_grounded",))
    probe = _tokens(stem) | set().union(*(_tokens(t) for t in correct_texts))
    if not probe:
        return GroundingVerdict(False, 0.0, ("not_grounded",))
    overlap = len(probe & page) / max(len(probe), 1)
    grounded = overlap >= min_score
    codes = () if grounded else ("not_grounded",)
    return GroundingVerdict(grounded, overlap, codes)
