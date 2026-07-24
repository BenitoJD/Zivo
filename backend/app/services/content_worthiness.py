"""Content Worthiness Gate Engine — skip non-content / empty / ad pages.

Design: docs/CONTENT_WORTHINESS_ENGINE.md
Version: qb.worth.v1
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WORTH_VERSION = "qb.worth.v1"

_AD_HINT = re.compile(
    r"\b(subscribe now|limited time offer|click here|sponsored content|advertisement)\b",
    re.I,
)


@dataclass(frozen=True)
class WorthinessVerdict:
    worthy: bool
    reason: str
    policy_version: str = WORTH_VERSION


def evaluate_worthiness(
    *,
    page_text: str | None = None,
    non_content: bool = False,
    empty: bool = False,
    ad_likely: bool = False,
    min_chars: int = 40,
) -> WorthinessVerdict:
    if non_content:
        return WorthinessVerdict(False, "non_content")
    if empty:
        return WorthinessVerdict(False, "empty")
    if ad_likely:
        return WorthinessVerdict(False, "ad_likely")
    text = (page_text or "").strip()
    if len(text) < min_chars:
        return WorthinessVerdict(False, "too_short")
    if _AD_HINT.search(text):
        return WorthinessVerdict(False, "ad_copy")
    return WorthinessVerdict(True, "ok")
