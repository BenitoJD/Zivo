"""SEO Gate Engine - usefulness triage + anti-repeat dedupe.

Design: docs/ENGINES.md (SEO Gate)
Version: qb.seo_gate.v1

Owns: whether a candidate is useful to strangers, and whether it is a near-dupe.
Plumbing (slug uniqueness DB loops, embedding calls, writer LLM) stays in
seo_dedupe / seo_writer / seo_cook.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

SEO_GATE_VERSION = "qb.seo_gate.v1"
DEFAULT_POLICY = "seo_gate_v1"

# Anti-repeat embedding cosine (owned here; seo_dedupe is plumbing).
NEAR_DUPE_COSINE = 0.85
DEFAULT_SOFT_MAX_PER_DAY = 20

UsefulnessReason = Literal[
    "ok",
    "too_short",
    "internal_markers",
    "personal_markers",
    "internal_filename",
    "no_useful_signal",
]

_INTERNAL_MARKERS = re.compile(
    r"\b("
    r"confidential|internal\s+only|do\s+not\s+(?:share|distribute|forward)|"
    r"proprietary|for\s+(?:internal|employee)\s+use|"
    r"meeting\s+notes?|standup\s+notes?|1\s*:\s*1\s+notes?|"
    r"action\s+items?\s+for\s+(?:me|us|team)|"
    r"password|api[_ ]?key|secret[_ ]?key|private\s+key|"
    r"ssn|salary|compensation\s+band|"
    r"todo:\s*|fixme:\s*|wip\b|"
    r"dear\s+team,|hi\s+team,"
    r")\b",
    re.I,
)

_PERSONAL_MARKERS = re.compile(
    r"\b("
    r"my\s+(?:boss|manager|spouse|wife|husband|kids?|landlord)|"
    r"remind\s+me\s+to|don'?t\s+forget\s+(?:to\s+)?(?:pick\s+up|call)|"
    r"grocery\s+list|personal\s+diary|journal\s+entry"
    r")\b",
    re.I,
)

_USEFUL_SIGNALS = re.compile(
    r"\b("
    r"how\s+(?:to|does|do)|why\s+(?:does|do|is)|what\s+is|"
    r"explain|concept|principle|theorem|algorithm|"
    r"system\s+design|architecture|database|cache|queue|"
    r"constitution|parliament|economy|inflation|policy|"
    r"interview|exam|upsc|practice|trade[- ]?off"
    r")\b",
    re.I,
)


@dataclass(frozen=True)
class SeoUsefulnessVerdict:
    useful: bool
    reason: UsefulnessReason
    policy: str = DEFAULT_POLICY
    policy_version: str = SEO_GATE_VERSION


@dataclass(frozen=True)
class SeoDedupeVerdict:
    ok: bool
    reason: str
    policy: str = DEFAULT_POLICY
    policy_version: str = SEO_GATE_VERSION


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_POLICY).strip().lower()
    if p in ("default", "seo", "triage"):
        return DEFAULT_POLICY
    return p or DEFAULT_POLICY


def evaluate_usefulness(
    text: str,
    *,
    filename: str = "",
    policy: str | None = None,
) -> SeoUsefulnessVerdict:
    """Sole usefulness seam before LLM rewrite."""
    pol = normalize_policy(policy)
    body = (text or "").strip()
    name = (filename or "").strip().lower()
    if len(body) < 400:
        return SeoUsefulnessVerdict(False, "too_short", policy=pol)
    if _INTERNAL_MARKERS.search(body) or _INTERNAL_MARKERS.search(name):
        return SeoUsefulnessVerdict(False, "internal_markers", policy=pol)
    if _PERSONAL_MARKERS.search(body):
        return SeoUsefulnessVerdict(False, "personal_markers", policy=pol)
    if any(
        tok in name
        for tok in ("password", "secrets", "1on1", "1-1", "standup", "payroll")
    ):
        return SeoUsefulnessVerdict(False, "internal_filename", policy=pol)
    if not _USEFUL_SIGNALS.search(body):
        if len(body) < 1200:
            return SeoUsefulnessVerdict(False, "no_useful_signal", policy=pol)
    return SeoUsefulnessVerdict(True, "ok", policy=pol)


def triage_usefulness(text: str, *, filename: str = "") -> tuple[bool, str]:
    """Compat tuple wrapper."""
    v = evaluate_usefulness(text, filename=filename)
    return v.useful, v.reason


def evaluate_dedupe(
    db: Session,
    *,
    fingerprint: str,
    title: str,
    lede: str,
    policy: str | None = None,
) -> SeoDedupeVerdict:
    """Sole anti-repeat seam. ok=False means reject.

    Fingerprint / embedding checks live in seo_dedupe plumbing; this facade
    owns the accept/reject verdict + policy_version.
    """
    pol = normalize_policy(policy)
    from app.services.seo_dedupe import check_dedupe

    ok, reason = check_dedupe(db, fingerprint=fingerprint, title=title, lede=lede)
    return SeoDedupeVerdict(ok=ok, reason=reason, policy=pol)


@dataclass(frozen=True)
class SeoPublishCapVerdict:
    allow: bool
    remaining: int
    soft_max: int
    published_today: int
    reason: str
    policy: str = DEFAULT_POLICY
    policy_version: str = SEO_GATE_VERSION


def evaluate_publish_cap(
    *,
    published_today: int,
    soft_max_per_day: int | None = None,
    policy: str | None = None,
) -> SeoPublishCapVerdict:
    """Daily soft publish ceiling before SEO cook spends LLM."""
    pol = normalize_policy(policy)
    soft = int(soft_max_per_day) if soft_max_per_day is not None else DEFAULT_SOFT_MAX_PER_DAY
    soft = max(1, soft)
    published = max(0, int(published_today))
    remaining = max(0, soft - published)
    if remaining <= 0:
        return SeoPublishCapVerdict(
            allow=False,
            remaining=0,
            soft_max=soft,
            published_today=published,
            reason="soft_max",
            policy=pol,
        )
    return SeoPublishCapVerdict(
        allow=True,
        remaining=remaining,
        soft_max=soft,
        published_today=published,
        reason="ok",
        policy=pol,
    )
