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

from app.engine_runtime import Pred, Rule, choose, first_match, pick

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


_POLICY_ALIASES = ("default", "seo", "triage")
_INTERNAL_FILENAME_TOKS = ("password", "secrets", "1on1", "1-1", "standup", "payroll")
_USEFUL_RULES = (
    Rule(when=(Pred("too_short", "truthy"),), action="too_short"),
    Rule(when=(Pred("internal", "truthy"),), action="internal_markers"),
    Rule(when=(Pred("personal", "truthy"),), action="personal_markers"),
    Rule(when=(Pred("internal_fn", "truthy"),), action="internal_filename"),
    Rule(
        when=(Pred("no_signal", "truthy"), Pred("signal_short", "truthy")),
        action="no_useful_signal",
    ),
    Rule(when=(), action="ok"),
)


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_POLICY).strip().lower()
    return pick(p in _POLICY_ALIASES, lambda: DEFAULT_POLICY, lambda: p or DEFAULT_POLICY)


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
    hit = first_match(
        _USEFUL_RULES,
        {
            "too_short": len(body) < SEO_USEFUL_MIN_CHARS,
            "internal": bool(_INTERNAL_MARKERS.search(body) or _INTERNAL_MARKERS.search(name)),
            "personal": bool(_PERSONAL_MARKERS.search(body)),
            "internal_fn": any(tok in name for tok in _INTERNAL_FILENAME_TOKS),
            "no_signal": not _USEFUL_SIGNALS.search(body),
            "signal_short": len(body) < SEO_USEFUL_SIGNAL_MIN_CHARS,
        },
    )
    reason: UsefulnessReason = hit.action  # type: ignore[assignment]
    return SeoUsefulnessVerdict(hit.action == "ok", reason, policy=pol)


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
    soft = pick(
        soft_max_per_day is not None,
        lambda: int(soft_max_per_day),
        lambda: DEFAULT_SOFT_MAX_PER_DAY,
    )
    soft = max(1, soft)
    published = max(0, int(published_today))
    remaining = max(0, soft - published)
    return pick(
        remaining <= 0,
        lambda: SeoPublishCapVerdict(
            allow=False,
            remaining=0,
            soft_max=soft,
            published_today=published,
            reason="soft_max",
            policy=pol,
        ),
        lambda: SeoPublishCapVerdict(
            allow=True,
            remaining=remaining,
            soft_max=soft,
            published_today=published,
            reason="ok",
            policy=pol,
        ),
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


_FORMAT_CONTRACTS: dict[str, SeoFormatContract] = {
    "faq": SeoFormatContract(
        "faq",
        (
            "Write an FAQ post. body_md with ## questions. "
            "Also fill faq_items as [{question, answer}, ...] (3-6 items)."
        ),
    ),
    "list": SeoFormatContract(
        "list",
        "Write a numbered list post (5-9 concrete points). body_md markdown.",
    ),
    "explainer": SeoFormatContract(
        "explainer",
        (
            "Write an explainer (800-1500 words target in body_md). "
            "Markdown with short headings."
        ),
    ),
}


def plan_article_format_contract(fmt: str) -> SeoFormatContract:
    """Prompt contract for the chosen article format (word band / item counts)."""
    return _FORMAT_CONTRACTS.get(fmt, _FORMAT_CONTRACTS["explainer"])


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
    picker = pick(rng is not None, lambda: rng, lambda: _random)

    def rolled() -> Literal["explainer", "faq", "list"]:
        roll = picker.random()
        return pick(
            roll < 0.70,
            lambda: "explainer",
            lambda: pick(roll < 0.85, lambda: "faq", lambda: "list"),
        )

    fmt: Literal["explainer", "faq", "list"] = pick(
        format_override in {"explainer", "faq", "list"},
        lambda: format_override,  # type: ignore[return-value]
        rolled,
    )
    kind = pick(
        stream == "system_design",
        lambda: "system_design",
        lambda: picker.choice(["practice", "signup", "system_design"]),
    )
    return SeoPresentationPlan(format=fmt, cta_kind=kind, policy=pol)


SD_DAILY_MIN = 1
_SD_DAILY_RULES = (
    Rule(when=(Pred("cook_enabled", "falsey"),), action="cook_disabled", extras={"cook": False}),
    Rule(when=(Pred("have_sd", "truthy"),), action="already_have_sd", extras={"cook": False}),
    Rule(when=(Pred("cap_allow", "falsey"),), action="soft_max", extras={"cook": False}),
    Rule(
        when=(),
        action="need_sd",
        extras={"cook": True, "source_order": ("problem", "topic")},
    ),
)


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
    hit = first_match(
        _SD_DAILY_RULES,
        {
            "cook_enabled": cook_enabled,
            "have_sd": int(sd_published_today) >= SD_DAILY_MIN,
            "cap_allow": cap_allow,
        },
    )
    return SdDailyCookPlan(
        bool(hit.extras["cook"]),
        hit.action,
        source_order=tuple(hit.extras.get("source_order") or ()),
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
    n = pick(batch_size is None, lambda: SEO_CANDIDATE_BATCH_DEFAULT, lambda: int(batch_size))
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
    return choose(
        (source_kind or "").strip().lower() == "newspaper",
        SEO_USEFUL_MIN_CHARS,
        SEO_UPLOAD_CANDIDATE_MIN_CHARS,
    )


def plan_seo_candidate_query_limit(*, source_kind: str, requested: int) -> int:
    """Hard cap on how many candidate rows one SEO query may load."""
    kind = (source_kind or "").strip().lower()
    cap = choose(
        kind == "newspaper",
        SEO_NEWSPAPER_CANDIDATE_QUERY_MAX,
        SEO_UPLOAD_CANDIDATE_QUERY_MAX,
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
    return pick(
        kind == "sd_bank",
        lambda: f"sd:{source_key}",
        lambda: pick(
            kind == "topic_queue",
            lambda: f"topic:{source_key}",
            lambda: default_fingerprint,
        ),
    )


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
    return pick(
        verdict.reason == "no_useful_signal",
        lambda: SeoUsefulnessVerdict(True, "ok", policy=verdict.policy),
        lambda: verdict,
    )


def digest_skip_reason(verdict: SeoUsefulnessVerdict) -> str | None:
    """Map a digest usefulness miss to the cook skip taxonomy."""
    return pick(
        verdict.useful,
        lambda: None,
        lambda: pick(
            verdict.reason == "too_short",
            lambda: "insufficient_text",
            lambda: verdict.reason,
        ),
    )


def should_bypass_digest_dedupe(*, force: bool, existing_post_id: Any) -> bool:
    """Force recook of an already-linked edition post skips the near-dupe gate."""
    return bool(force) and existing_post_id is not None


RETRYABLE_EDITION_DIGEST_REASONS = frozenset({"write_failed"})
SEO_COOK_TICK_DEFAULT = 3
SEO_COOK_TICK_MAX = 5
EditionDigestSkipStatus = Literal["failed", "skipped"]


def evaluate_edition_digest_skip_status(reason: str) -> EditionDigestSkipStatus:
    """Transient write failures stay retryable; other skips are terminal."""
    return choose(str(reason) in RETRYABLE_EDITION_DIGEST_REASONS, "failed", "skipped")


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
    lo = max(1, int(min_count))
    hi = max(lo, int(max_count))
    seen: set[str] = set()
    unique: list[Any] = []
    for aid, ck in rows:
        unique += choose(ck in seen, [], [aid])
        seen.add(ck)
    picked = unique[:hi]
    need = max(0, lo - len(picked))
    extras = list(filter(lambda aid: aid not in picked, (aid for aid, _ck in rows)))[:need]
    return (picked + extras)[:hi]


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
    cooked = list(filter(lambda p: int(p.mcq_count) > 0, pages))
    ranked = pick(
        bool(cooked),
        lambda: sorted(cooked, key=lambda p: (-int(p.mcq_count), int(p.page))),
        lambda: sorted(list(filter(lambda p: p.worthy, pages)), key=lambda p: int(p.page)),
    )
    parts: list[str] = []
    total = [0]

    def add_page(page: NewspaperDigestPage) -> None:
        room = cap - total[0]
        chunk = choose(len(page.text) <= room, page.text, page.text[:room])
        parts.append(chunk)
        total[0] += len(chunk) + 2

    list(map(add_page, filter(lambda _p: total[0] < cap, ranked)))
    return "\n\n".join(parts)


def plan_seo_faq_item_cap() -> int:
    """How many FAQ Q/A pairs a cooked SEO post may keep."""
    return SEO_FAQ_ITEM_MAX


def plan_seo_related_mcq_query_limit(requested: int | None = None) -> int:
    """Cap on related-assertion lookup when attaching MCQs to an SEO post."""
    n = pick(requested is None, lambda: SEO_RELATED_MCQ_QUERY_DEFAULT, lambda: int(requested))
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


def evaluate_seo_cook_enabled(settings: dict[str, Any] | None) -> bool:
    """Master cook switch from qb.seo_settings."""
    return bool((settings or {}).get("cook_enabled"))


_EDITION_DIGEST_PREFLIGHT_RULES = (
    Rule(when=(Pred("no_ed", "truthy"),), action="edition_not_found"),
    Rule(when=(Pred("not_ready", "truthy"),), action="edition_not_ready"),
    Rule(
        when=(Pred("already", "truthy"), Pred("force", "falsey")),
        action="already_published",
    ),
    Rule(when=(), action="ok"),
)


def evaluate_edition_digest_preflight(
    ed: dict[str, Any] | None,
    *,
    force: bool = False,
) -> str:
    """Whether an edition may enter the digest cook, or why to skip."""
    return first_match(
        _EDITION_DIGEST_PREFLIGHT_RULES,
        {
            "no_ed": not ed,
            "not_ready": bool(ed) and ed.get("status") != "ready",
            "already": bool(ed)
            and ed.get("blog_status") == "published"
            and bool(ed.get("blog_post_id")),
            "force": bool(force),
        },
    ).action


_EDITION_BLOG_LINK_RULES = (
    Rule(when=(Pred("no_ed", "truthy"),), action="missing"),
    Rule(
        when=(Pred("has_post", "truthy"), Pred("post_live", "truthy")),
        action="already_linked",
    ),
    Rule(when=(Pred("fp_live", "truthy"),), action="link_existing"),
    Rule(when=(), action="continue"),
)


def evaluate_edition_blog_link(
    *,
    has_edition: bool,
    has_post_id: bool,
    post_published: bool,
    fingerprint_published: bool,
) -> str:
    """Repair vs short-circuit when a digest post is already live."""
    return first_match(
        _EDITION_BLOG_LINK_RULES,
        {
            "no_ed": not has_edition,
            "has_post": has_post_id,
            "post_live": post_published,
            "fp_live": fingerprint_published,
        },
    ).action


_SKIP_EDITION_LINK_RULES = (
    Rule(
        when=(Pred("has_post", "truthy"), Pred("post_live", "truthy")),
        action="keep_published",
    ),
    Rule(when=(), action="record_skip"),
)


def evaluate_edition_skip_link(
    *,
    has_post_id: bool,
    post_published: bool,
) -> str:
    """Keep a live digest link on skip, otherwise record the skip reason."""
    return first_match(
        _SKIP_EDITION_LINK_RULES,
        {"has_post": has_post_id, "post_live": post_published},
    ).action
