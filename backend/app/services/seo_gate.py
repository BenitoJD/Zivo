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
from typing import Any, Literal

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


@dataclass(frozen=True)
class SeoPresentationPlan:
    format: Literal["explainer", "faq", "list"]
    cta_kind: str
    policy: str = DEFAULT_POLICY
    policy_version: str = SEO_GATE_VERSION


def plan_article_presentation(
    stream: str,
    *,
    format_override: str | None = None,
    rng: Any | None = None,
    policy: str | None = None,
) -> SeoPresentationPlan:
    """Editorial mix (~70% explainer, 15% faq, 15% list) and CTA kind."""
    import random as _random

    pol = normalize_policy(policy)
    picker = rng if rng is not None else _random
    if format_override in {"explainer", "faq", "list"}:
        fmt = format_override  # type: Literal["explainer", "faq", "list"]
    else:
        roll = picker.random()
        if roll < 0.70:
            fmt = "explainer"
        elif roll < 0.85:
            fmt = "faq"
        else:
            fmt = "list"
    kind = "system_design" if stream == "system_design" else picker.choice(["practice", "signup", "system_design"])
    return SeoPresentationPlan(format=fmt, cta_kind=kind, policy=pol)


SD_DAILY_MIN = 1


@dataclass(frozen=True)
class SdDailyCookPlan:
    cook: bool
    reason: str
    source_order: tuple[str, ...] = ()
    policy: str = DEFAULT_POLICY
    policy_version: str = SEO_GATE_VERSION


def plan_sd_daily_cook(
    *,
    cook_enabled: bool,
    sd_published_today: int,
    cap_allow: bool,
    policy: str | None = None,
) -> SdDailyCookPlan:
    """At least one system-design post per IST day; problem bank before topic queue."""
    pol = normalize_policy(policy)
    if not cook_enabled:
        return SdDailyCookPlan(False, "cook_disabled", policy=pol)
    if int(sd_published_today) >= SD_DAILY_MIN:
        return SdDailyCookPlan(False, "already_have_sd", policy=pol)
    if not cap_allow:
        return SdDailyCookPlan(False, "soft_max", policy=pol)
    return SdDailyCookPlan(
        True,
        "need_sd",
        source_order=("problem", "topic"),
        policy=pol,
    )


SEO_MCQ_ATTACH_MIN = 4
SEO_MCQ_ATTACH_MAX = 6
NEWSPAPER_DIGEST_MAX_CHARS = 12_000


@dataclass(frozen=True)
class EmbeddingNearDupeVerdict:
    is_dupe: bool
    similarity: float
    threshold: float = NEAR_DUPE_COSINE
    policy: str = DEFAULT_POLICY
    policy_version: str = SEO_GATE_VERSION


def evaluate_embedding_near_dupe(
    similarity: float,
    *,
    threshold: float = NEAR_DUPE_COSINE,
    policy: str | None = None,
) -> EmbeddingNearDupeVerdict:
    """Reject SEO cook when published cosine meets the near-dupe floor."""
    pol = normalize_policy(policy)
    sim = float(similarity)
    floor = float(threshold)
    return EmbeddingNearDupeVerdict(
        is_dupe=sim >= floor,
        similarity=sim,
        threshold=floor,
        policy=pol,
    )


def evaluate_newspaper_seo_candidate(
    page_text: str,
    *,
    db: Session | None = None,
) -> bool:
    """Newspaper page may enter the SEO cook queue."""
    from app.services.content_worthiness import evaluate_worthiness

    return evaluate_worthiness(page_text=page_text, newspaper=True, db=db).worthy


def evaluate_seo_mcq_attach_ready(
    attached_count: int,
    *,
    min_count: int = SEO_MCQ_ATTACH_MIN,
) -> bool:
    """True when related MCQs already fill the attach floor (skip generate)."""
    return int(attached_count) >= max(1, int(min_count))


def evaluate_digest_source_ready(text: str, *, filename: str = "") -> SeoUsefulnessVerdict:
    """Edition digest uses the usefulness gate; syllabus keywords are optional."""
    verdict = evaluate_usefulness(text, filename=filename)
    if verdict.reason == "no_useful_signal":
        return SeoUsefulnessVerdict(True, "ok", policy=verdict.policy)
    return verdict


def digest_skip_reason(verdict: SeoUsefulnessVerdict) -> str | None:
    """Map a digest usefulness miss to the cook skip taxonomy."""
    if verdict.useful:
        return None
    if verdict.reason == "too_short":
        return "insufficient_text"
    return verdict.reason


def plan_seo_mcq_attach(
    rows: list[tuple[Any, str]],
    *,
    min_count: int = SEO_MCQ_ATTACH_MIN,
    max_count: int = SEO_MCQ_ATTACH_MAX,
    policy: str | None = None,
) -> list[Any]:
    """Prefer distinct concept keys, then backfill to min_count."""
    del policy
    if not rows:
        return []
    lo = max(1, int(min_count))
    hi = max(lo, int(max_count))
    picked: list[Any] = []
    seen: set[str] = set()
    for aid, ck in rows:
        if ck in seen:
            continue
        seen.add(ck)
        picked.append(aid)
        if len(picked) >= hi:
            break
    if len(picked) < lo:
        for aid, _ck in rows:
            if aid in picked:
                continue
            picked.append(aid)
            if len(picked) >= lo:
                break
    return picked[:hi]


@dataclass(frozen=True)
class NewspaperDigestPage:
    page: int
    text: str
    mcq_count: int
    worthy: bool


def plan_newspaper_digest(
    pages: list[NewspaperDigestPage],
    *,
    max_chars: int = NEWSPAPER_DIGEST_MAX_CHARS,
    policy: str | None = None,
) -> str:
    """Rank cooked MCQ pages first, else worthy fallback; cap digest length."""
    del policy
    cap = max(1, int(max_chars))
    cooked = [p for p in pages if int(p.mcq_count) > 0]
    if cooked:
        ranked = sorted(cooked, key=lambda p: (-int(p.mcq_count), int(p.page)))
    else:
        ranked = sorted([p for p in pages if p.worthy], key=lambda p: int(p.page))
    parts: list[str] = []
    total = 0
    for page in ranked:
        if total >= cap:
            break
        room = cap - total
        chunk = page.text if len(page.text) <= room else page.text[:room]
        parts.append(chunk)
        total += len(chunk) + 2
    return "\n\n".join(parts)
