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
SEO_USEFUL_MIN_CHARS = 400
SEO_USEFUL_SIGNAL_MIN_CHARS = 1200
SEO_UPLOAD_CANDIDATE_MIN_CHARS = 500
SEO_NEWSPAPER_CANDIDATE_QUERY_MAX = 20
SEO_UPLOAD_CANDIDATE_QUERY_MAX = 10
SEO_DIGEST_MIN_WORDS = 400
SEO_DIGEST_MAX_WORDS = 900
SEO_DIGEST_SOURCE_CHARS = 16000

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
    if len(body) < SEO_USEFUL_MIN_CHARS:
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
        if len(body) < SEO_USEFUL_SIGNAL_MIN_CHARS:
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


@dataclass(frozen=True)
class SeoFormatContract:
    format: Literal["explainer", "faq", "list"]
    prompt_rule: str
    policy_version: str = SEO_GATE_VERSION


def plan_article_format_contract(fmt: str) -> SeoFormatContract:
    """Prompt contract for the chosen article format (word band / item counts)."""
    kind: Literal["explainer", "faq", "list"]
    if fmt == "faq":
        kind = "faq"
        rule = (
            "Write an FAQ post. body_md with ## questions. "
            "Also fill faq_items as [{question, answer}, ...] (3-6 items)."
        )
    elif fmt == "list":
        kind = "list"
        rule = "Write a numbered list post (5-9 concrete points). body_md markdown."
    else:
        kind = "explainer"
        rule = (
            "Write an explainer (800-1500 words target in body_md). "
            "Markdown with short headings."
        )
    return SeoFormatContract(kind, rule)


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
SEO_MCQ_GENERATION_ATTEMPTS = 2
SEO_RELATED_MCQ_QUERY_MAX = 10
SEO_RELATED_MCQ_QUERY_DEFAULT = 5
SEO_CANDIDATE_BATCH_DEFAULT = 5
SEO_FAQ_ITEM_MAX = 8
SEO_ARTICLE_SOURCE_CHARS = 12_000
SEO_MCQ_PAGE_CHARS = 8_000
SEO_SD_SOURCE_CHARS = 4_000
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


SEO_CANDIDATE_SOURCES = ("newspaper", "upload")


@dataclass(frozen=True)
class SeoCandidateSchedule:
    batch_size: int
    sources: tuple[str, ...]
    policy: str = DEFAULT_POLICY
    policy_version: str = SEO_GATE_VERSION


def plan_seo_candidate_schedule(
    *,
    batch_size: int | None = None,
    policy: str | None = None,
) -> SeoCandidateSchedule:
    """Newspaper pages first, then uploads, until batch_size."""
    pol = normalize_policy(policy)
    n = SEO_CANDIDATE_BATCH_DEFAULT if batch_size is None else int(batch_size)
    cap = max(1, n)
    return SeoCandidateSchedule(
        batch_size=cap,
        sources=SEO_CANDIDATE_SOURCES,
        policy=pol,
    )


def seo_candidate_slots_remaining(*, accepted: int, batch_size: int) -> int:
    """How many more SEO cook candidates this tick may accept."""
    return max(0, int(batch_size) - int(accepted))


def seo_candidate_min_chars(source_kind: str) -> int:
    """Pre-filter floor before a newspaper page or upload enters the SEO cook queue."""
    if (source_kind or "").strip().lower() == "newspaper":
        return SEO_USEFUL_MIN_CHARS
    return SEO_UPLOAD_CANDIDATE_MIN_CHARS


def plan_seo_candidate_query_limit(*, source_kind: str, requested: int) -> int:
    """Hard cap on how many candidate rows one SEO query may load."""
    kind = (source_kind or "").strip().lower()
    cap = (
        SEO_NEWSPAPER_CANDIDATE_QUERY_MAX
        if kind == "newspaper"
        else SEO_UPLOAD_CANDIDATE_QUERY_MAX
    )
    return max(1, min(int(requested), int(cap)))


@dataclass(frozen=True)
class SeoDigestWriterContract:
    min_words: int
    max_words: int
    source_chars: int
    policy_version: str = SEO_GATE_VERSION


def plan_seo_digest_writer_contract() -> SeoDigestWriterContract:
    """Word band and source trim for the edition-digest rewrite prompt."""
    return SeoDigestWriterContract(
        SEO_DIGEST_MIN_WORDS,
        SEO_DIGEST_MAX_WORDS,
        SEO_DIGEST_SOURCE_CHARS,
    )


def plan_seo_fingerprint(
    *,
    source_kind: str,
    source_key: str,
    default_fingerprint: str,
) -> str:
    """Stable fingerprints for bank/topic sources so we do not recook the same item."""
    kind = (source_kind or "").strip().lower()
    if kind == "sd_bank":
        return f"sd:{source_key}"
    if kind == "topic_queue":
        return f"topic:{source_key}"
    return default_fingerprint


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


def should_bypass_digest_dedupe(*, force: bool, existing_post_id: Any) -> bool:
    """Force recook of an already-linked edition post skips the near-dupe gate."""
    return bool(force) and existing_post_id is not None


RETRYABLE_EDITION_DIGEST_REASONS = frozenset({"write_failed"})
SEO_COOK_TICK_DEFAULT = 3
SEO_COOK_TICK_MAX = 5
EditionDigestSkipStatus = Literal["failed", "skipped"]


def evaluate_edition_digest_skip_status(reason: str) -> EditionDigestSkipStatus:
    """Transient write failures stay retryable; other skips are terminal."""
    if str(reason) in RETRYABLE_EDITION_DIGEST_REASONS:
        return "failed"
    return "skipped"


def plan_seo_cook_tick(
    *,
    limit: int,
    remaining: int,
    max_batch: int = SEO_COOK_TICK_MAX,
) -> int:
    """How many SEO candidates this tick may cook (cap, remaining slots, hard max)."""
    return max(0, min(int(limit), int(remaining), int(max_batch)))


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


def plan_seo_faq_item_cap() -> int:
    """How many FAQ Q/A pairs a cooked SEO post may keep."""
    return SEO_FAQ_ITEM_MAX


def plan_seo_related_mcq_query_limit(requested: int | None = None) -> int:
    """Cap on related-assertion lookup when attaching MCQs to an SEO post."""
    n = SEO_RELATED_MCQ_QUERY_DEFAULT if requested is None else int(requested)
    return max(1, min(n, SEO_RELATED_MCQ_QUERY_MAX))


def plan_seo_article_source_chars() -> int:
    """How much scrubbed source an SEO article rewrite may send to the LLM."""
    return SEO_ARTICLE_SOURCE_CHARS


def plan_seo_mcq_page_chars() -> int:
    """How much post body to treat as the page text when cooking SEO MCQs."""
    return SEO_MCQ_PAGE_CHARS


def plan_seo_sd_source_chars() -> int:
    """How much SD-bank reference design to clip into an SEO candidate."""
    return SEO_SD_SOURCE_CHARS


@dataclass(frozen=True)
class SeoMcqAttachPlan:
    target_count: int
    min_count: int
    max_count: int
    generation_attempts: int
    policy_version: str = SEO_GATE_VERSION


def plan_seo_mcq_attach_defaults() -> SeoMcqAttachPlan:
    """How many MCQs an SEO post should attach, and SEO-only generate retries."""
    return SeoMcqAttachPlan(
        SEO_MCQ_ATTACH_MIN,
        SEO_MCQ_ATTACH_MIN,
        SEO_MCQ_ATTACH_MAX,
        SEO_MCQ_GENERATION_ATTEMPTS,
    )


def plan_seo_digest_backfill_batch() -> int:
    """How many unlinked newspaper editions one digest-backfill tick may enqueue."""
    return plan_seo_candidate_schedule().batch_size
