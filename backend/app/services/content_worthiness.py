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

WORTH_VERSION = "qb.worth.v1"
DEFAULT_WORTH_POLICY = "worth_v1"

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


@dataclass(frozen=True)
class WorthinessVerdict:
    worthy: bool
    reason: str
    policy: str = DEFAULT_WORTH_POLICY
    policy_version: str = WORTH_VERSION
    details: str = ""


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
) -> WorthinessVerdict:
    """Sole worthiness seam. Orchestration must not invent parallel skip ladders."""
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

        verdict, rationale = newspaper_page_verdict(text)
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
