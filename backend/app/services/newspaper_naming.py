"""Adaptive newspaper PDF naming — aliases first, then LLM, then heuristic.

Filenames and captions change over time. Never hardcode paper abbreviations
as the primary identity path (TH/ET/…). Learn mappings into
``qb.newspaper_paper_alias`` only after a high-confidence, conflict-free LLM hit.

Brand-token families below are a *poison guard* only: they block cross-brand
alias learning (e.g. BS → The Hindu), not a classifier lookup table.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.repositories import newspaper as newspaper_repo
from app.services.llm_json import extract_json_obj

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
# Accept inventing a new brand; learning aliases needs ``_LLM_LEARN_CONFIDENCE``.
_LLM_MIN_CONFIDENCE = 0.35
_LLM_LEARN_CONFIDENCE = 0.8

# Conflict guard — tokens that clearly name a brand. Used to reject cross-maps.
_BRAND_TOKEN_FAMILIES: dict[str, frozenset[str]] = {
    "the-hindu": frozenset({"th", "thehindu", "hindu"}),
    "business-standard": frozenset({"bs", "businessstandard"}),
    "hindustan-times": frozenset({"ht", "hindustantimes"}),
    "financial-express": frozenset({"fe", "financialexpress"}),
    "mint": frozenset({"mint", "livemint"}),
    "the-economic-times": frozenset({"et", "economictimes", "theeconomictimes"}),
    "new-indian-express": frozenset({"nie", "newindianexpress", "indianexpress"}),
    "deccan-chronicle": frozenset({"dc", "deccanchronicle"}),
}
_TOKEN_TO_BRAND: dict[str, str] = {
    tok: slug for slug, toks in _BRAND_TOKEN_FAMILIES.items() for tok in toks
}

# Common city / noise tokens to strip when guessing paper title from a filename.
_LOCATION_TOKENS = {
    "kolkata",
    "calcutta",
    "delhi",
    "newdelhi",
    "mumbai",
    "bombay",
    "chennai",
    "madras",
    "bengaluru",
    "bangalore",
    "hyderabad",
    "pune",
    "ahmedabad",
    "jaipur",
    "lucknow",
    "chandigarh",
    "guwahati",
    "patna",
    "bhopal",
    "indore",
    "kochi",
    "cochin",
    "cuttack",
    "hubli",
    "erode",
    "coimbatore",
    "international",
    "epaper",
    "ebook",
    "premium",
    "english",
    "edition",
    "pdf",
}

_DATE_PATTERNS = [
    re.compile(r"(?P<y>20\d{2})[-_.](?P<m>\d{1,2})[-_.](?P<d>\d{1,2})"),
    re.compile(r"(?P<d>\d{1,2})[-_.](?P<m>\d{1,2})[-_.](?P<y>20\d{2})"),
    re.compile(r"(?P<d>\d{1,2})[-_.](?P<m>\d{1,2})[-_.](?P<y>\d{2})\b"),
]


@dataclass(frozen=True)
class ParsedEdition:
    paper_slug: str
    paper_title: str
    edition_date: date
    location_raw: str
    source: str  # alias | llm | heuristic (+ date source)


def _normalize_blob(filename: str, caption: str) -> str:
    base = (filename or "").rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        base = base[:-4]
    return f"{base} {caption or ''}".strip()


def _parse_date(blob: str) -> date | None:
    for pat in _DATE_PATTERNS:
        m = pat.search(blob.replace(" ", "_"))
        if not m:
            continue
        y = int(m.group("y"))
        if y < 100:
            y += 2000
        try:
            return date(y, int(m.group("m")), int(m.group("d")))
        except ValueError:
            continue
    return None


def _guess_location(tokens: list[str]) -> str:
    noise = {
        "epaper",
        "ebook",
        "premium",
        "english",
        "edition",
        "pdf",
        "international",
    }
    for t in tokens:
        if t.lower() in _LOCATION_TOKENS and t.lower() not in noise:
            return t
    return ""


def _guess_title(tokens: list[str]) -> str:
    kept: list[str] = []
    for t in tokens:
        low = t.lower()
        if low in _LOCATION_TOKENS:
            continue
        if re.fullmatch(r"\d{1,4}", t):
            continue
        if re.fullmatch(r"20\d{2}", t):
            continue
        kept.append(t)
    if not kept:
        return "Newspaper"
    parts = []
    for t in kept[:6]:
        parts.append(t.upper() if len(t) <= 3 else t.title())
    return " ".join(parts)


def _norm_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (raw or "").lower())


def _alias_lookup_keys(*, guessed_title: str, blob: str, tokens: list[str]) -> list[str]:
    keys = [guessed_title, blob]
    if tokens:
        keys.append(tokens[0])
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        norm = _norm_key(k)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(k)
    return out


def _match_known_brand(db: Session, paper_title: str) -> tuple[str, str, bool]:
    """Map LLM title onto an existing brand by exact slug/title only.

    Returns (slug, title, exact_catalog_hit). Soft/partial substring matches are
    intentionally rejected — they poisoned aliases (e.g. BS → The Hindu).
    """
    title = re.sub(r"\s+", " ", (paper_title or "").strip()) or "Newspaper"
    slug, canon = newspaper_repo.make_paper_identity(title)
    title_l = title.lower()
    for b in newspaper_repo.list_brands(db):
        b_title = str(b.get("paper_title") or "")
        b_slug = str(b.get("paper_slug") or "")
        if b_slug == slug or b_title.lower() == title_l:
            return b_slug, b_title or canon, True
    return slug, canon, False


def _filename_brand_slugs(tokens: list[str], *, blob: str = "") -> set[str]:
    """Brand families clearly named by filename tokens (conflict guard)."""
    found: set[str] = set()
    norms = {_norm_key(t) for t in tokens if t}
    blob_n = _norm_key(blob)
    if blob_n:
        norms.add(blob_n)

    loc_norms = {_norm_key(t) for t in _LOCATION_TOKENS}

    for norm in norms:
        brand = _TOKEN_TO_BRAND.get(norm)
        if brand:
            found.add(brand)
            continue
        # Glued forms: bsahmedabad24072026, thdelhi, …
        for tok, slug in _TOKEN_TO_BRAND.items():
            if len(tok) < 2 or not norm.startswith(tok) or norm == tok:
                continue
            rest = norm[len(tok) :]
            if rest.isdigit() or any(rest.startswith(loc) for loc in loc_norms):
                found.add(slug)
    return found


def _identity_conflicts_filename(
    *, paper_slug: str, tokens: list[str], blob: str
) -> str | None:
    """Return conflicting brand slug if filename clearly names another paper."""
    named = _filename_brand_slugs(tokens, blob=blob)
    if not named:
        return None
    if paper_slug in named and len(named) == 1:
        return None
    others = named - {paper_slug}
    if others:
        return sorted(others)[0]
    return None


def _learnable_alias_keys(
    *,
    paper_slug: str,
    guessed_title: str,
    tokens: list[str],
) -> list[str]:
    """Only brand-family tokens present in the filename — never cities/dates/noise."""
    family = _BRAND_TOKEN_FAMILIES.get(paper_slug, frozenset())
    if not family:
        # Unknown brand: only learn the full guessed title if it's multi-char and
        # not a location/date — never a bare city token.
        key = _norm_key(guessed_title)
        if (
            key
            and len(key) >= 3
            and key not in {_norm_key(t) for t in _LOCATION_TOKENS}
            and not key.isdigit()
            and not re.fullmatch(r"\d{6,8}", key)
        ):
            return [guessed_title]
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for raw in [guessed_title, *tokens]:
        norm = _norm_key(raw)
        if not norm or norm in seen:
            continue
        if norm in {_norm_key(t) for t in _LOCATION_TOKENS}:
            continue
        if norm.isdigit() or re.fullmatch(r"\d{6,8}", norm):
            continue
        if norm not in family:
            continue
        seen.add(norm)
        candidates.append(raw)
    return candidates


def _learn_aliases(
    db: Session,
    *,
    paper_slug: str,
    paper_title: str,
    guessed_title: str,
    tokens: list[str],
    confidence: float,
    exact_catalog_hit: bool,
    blob: str,
) -> None:
    """Cache learned mappings — high confidence + no cross-brand conflict only."""
    if confidence < _LLM_LEARN_CONFIDENCE and not (
        exact_catalog_hit and confidence >= _LLM_MIN_CONFIDENCE
    ):
        logger.info(
            "skip alias learn paper=%s confidence=%s exact=%s",
            paper_slug,
            confidence,
            exact_catalog_hit,
        )
        return
    if _identity_conflicts_filename(paper_slug=paper_slug, tokens=tokens, blob=blob):
        logger.info("skip alias learn paper=%s — filename brand conflict", paper_slug)
        return
    for key in _learnable_alias_keys(
        paper_slug=paper_slug, guessed_title=guessed_title, tokens=tokens
    ):
        newspaper_repo.upsert_alias(
            db, alias_key=key, paper_slug=paper_slug, paper_title=paper_title
        )


def _known_brands_prompt(db: Session) -> str:
    brands = newspaper_repo.list_brands(db)
    if not brands:
        return "(none yet — invent a clear canonical English title)"
    lines = []
    for b in brands:
        flag = "enabled" if b.get("enabled") else "disabled"
        lines.append(f"- {b['paper_title']} (slug={b['paper_slug']}, {flag})")
    return "\n".join(lines)


async def _llm_identify_paper_async(
    db: Session,
    *,
    filename: str,
    caption: str,
    tokens: list[str] | None = None,
    blob: str = "",
) -> tuple[str, str, float, bool] | None:
    """Ask LLM what newspaper this PDF is. No hardcoded abbreviation tables.

    Caller must hold the LLM slot (``complete_chat`` / ``run_coro_in_worker`` /
    ``llm_slot_async``).

    Returns (slug, title, confidence, exact_catalog_hit) or None.
    """
    from app.services.llm_registry import default_chat_model_id
    from app.services.llm_router import acomplete_chat

    system = (
        "You identify Indian / English newspaper brands from Telegram PDF filenames "
        "and captions. Filenames change often (abbreviations, cities, dates). "
        "Expand common abbreviations using world knowledge (e.g. brand initials). "
        "Never treat a city or date as the newspaper name. "
        "Prefer matching a known catalog brand when the file clearly refers to it. "
        "When the filename clearly matches a known brand (including common initials), "
        "set confidence >= 0.8. "
        "Never map a clear other-brand abbreviation onto a different catalog brand "
        "(e.g. BS/Business Standard, HT, FE, Mint, ET are not The Hindu). "
        "Reply with JSON only — no prose."
    )
    user = (
        f"Known brands in our catalog:\n{_known_brands_prompt(db)}\n\n"
        f"Filename: {filename or '(none)'}\n"
        f"Caption: {caption or '(none)'}\n\n"
        'Return JSON only: {"paper_title":"<canonical English newspaper name>",'
        '"confidence":<0.0-1.0>}'
    )
    try:
        model_id = default_chat_model_id(db)
        raw = await acomplete_chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            db,
            model_id=model_id,
            log_tag="newspaper_paper_id",
        )
    except Exception:
        logger.exception("newspaper paper-id LLM failed")
        return None

    data = extract_json_obj(raw)
    title = str(data.get("paper_title") or "").strip()
    try:
        confidence = float(data.get("confidence") if data.get("confidence") is not None else 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not title:
        logger.info("newspaper paper-id empty title confidence=%s", confidence)
        return None
    slug, canon, exact_hit = _match_known_brand(db, title)
    tok_list = tokens if tokens is not None else [
        t for t in re.split(r"[_\-\s.]+", _normalize_blob(filename, caption)) if t
    ]
    blob_s = blob or _normalize_blob(filename, caption)
    conflict = _identity_conflicts_filename(
        paper_slug=slug, tokens=tok_list, blob=blob_s
    )
    if conflict:
        logger.info(
            "newspaper paper-id rejected title=%r slug=%s — filename names %s",
            title,
            slug,
            conflict,
        )
        return None
    # Exact catalog match may under-score confidence; soft/partial never accepted.
    if not exact_hit and confidence < _LLM_MIN_CONFIDENCE:
        logger.info(
            "newspaper paper-id rejected title=%r confidence=%s",
            title,
            confidence,
        )
        return None
    return slug, canon, confidence, exact_hit


def _llm_identify_paper_sync(
    db: Session,
    *,
    filename: str,
    caption: str,
    tokens: list[str] | None = None,
    blob: str = "",
) -> tuple[str, str, float, bool] | None:
    """Sync path — ``run_coro_in_worker`` owns the LLM slot."""
    from app.services.llm_sync import run_coro_in_worker

    try:
        return run_coro_in_worker(
            _llm_identify_paper_async(
                db,
                filename=filename,
                caption=caption,
                tokens=tokens,
                blob=blob,
            )
        )
    except Exception:
        logger.exception("newspaper paper-id sync LLM failed")
        return None


def _finish_parse(
    db: Session,
    *,
    filename: str,
    caption: str,
    edition_date: date,
    date_source: str,
    location_raw: str,
    guessed_title: str,
    blob: str,
    tokens: list[str],
    llm_identity: tuple[str, str] | tuple[str, str, float, bool] | None,
    allow_llm: bool,
) -> ParsedEdition:
    for raw in _alias_lookup_keys(guessed_title=guessed_title, blob=blob, tokens=tokens):
        alias = newspaper_repo.resolve_alias(db, raw)
        if alias:
            conflict = _identity_conflicts_filename(
                paper_slug=alias[0], tokens=tokens, blob=blob
            )
            if conflict:
                logger.warning(
                    "ignoring poisoned alias key=%r -> %s (filename names %s)",
                    raw,
                    alias[0],
                    conflict,
                )
                newspaper_repo.delete_alias(db, raw)
                continue
            return ParsedEdition(
                paper_slug=alias[0],
                paper_title=alias[1],
                edition_date=edition_date,
                location_raw=location_raw,
                source=f"alias+{date_source}",
            )

    identity = llm_identity
    confidence = _LLM_LEARN_CONFIDENCE
    exact_hit = True
    if identity is not None and len(identity) == 4:
        slug, title, confidence, exact_hit = identity  # type: ignore[misc]
        identity = (slug, title)
    elif identity is not None and len(identity) == 2:
        # Test/injected identity — treat as exact high-confidence unless conflict.
        slug, title = identity  # type: ignore[misc]
        conflict = _identity_conflicts_filename(
            paper_slug=slug, tokens=tokens, blob=blob
        )
        if conflict:
            logger.info(
                "injected identity %s rejected — filename names %s",
                slug,
                conflict,
            )
            identity = None
        else:
            confidence = _LLM_LEARN_CONFIDENCE
            exact_hit = True

    if identity is None and allow_llm:
        rich = _llm_identify_paper_sync(
            db, filename=filename, caption=caption, tokens=tokens, blob=blob
        )
        if rich:
            slug, title, confidence, exact_hit = rich
            identity = (slug, title)

    if identity:
        slug, title = identity
        _learn_aliases(
            db,
            paper_slug=slug,
            paper_title=title,
            guessed_title=guessed_title,
            tokens=tokens,
            confidence=confidence,
            exact_catalog_hit=exact_hit,
            blob=blob,
        )
        return ParsedEdition(
            paper_slug=slug,
            paper_title=title,
            edition_date=edition_date,
            location_raw=location_raw,
            source=f"llm+{date_source}",
        )

    slug, title = newspaper_repo.make_paper_identity(guessed_title)
    # Do NOT upsert heuristic aliases — short tokens like "TH" would poison the table.
    return ParsedEdition(
        paper_slug=slug,
        paper_title=title,
        edition_date=edition_date,
        location_raw=location_raw,
        source=f"heuristic+{date_source}",
    )


def _date_and_tokens(
    *,
    filename: str,
    caption: str,
    message_date: datetime,
) -> tuple[str, list[str], date, str, str, str]:
    blob = _normalize_blob(filename, caption)
    tokens = [t for t in re.split(r"[_\-\s.]+", blob) if t]
    edition_date = _parse_date(blob)
    date_source = "heuristic"
    if edition_date is None:
        md = message_date
        if md.tzinfo is None:
            md = md.replace(tzinfo=timezone.utc)
        edition_date = md.astimezone(_IST).date()
        date_source = "message_date"
    location_raw = _guess_location(tokens)
    guessed_title = _guess_title(tokens)
    return blob, tokens, edition_date, date_source, location_raw, guessed_title


def parse_edition_meta(
    db: Session,
    *,
    filename: str,
    caption: str,
    message_date: datetime,
    llm_identity: tuple[str, str] | None = None,
    skip_llm: bool = False,
) -> ParsedEdition:
    """Sync parse. Tests: ``skip_llm=True`` or pass ``llm_identity``."""
    blob, tokens, edition_date, date_source, location_raw, guessed_title = _date_and_tokens(
        filename=filename, caption=caption, message_date=message_date
    )
    return _finish_parse(
        db,
        filename=filename,
        caption=caption,
        edition_date=edition_date,
        date_source=date_source,
        location_raw=location_raw,
        guessed_title=guessed_title,
        blob=blob,
        tokens=tokens,
        llm_identity=llm_identity,
        allow_llm=not skip_llm and llm_identity is None,
    )


async def parse_edition_meta_async(
    db: Session,
    *,
    filename: str,
    caption: str,
    message_date: datetime,
) -> ParsedEdition:
    """Async parse for Telethon ingest — awaits LLM without nested event loops."""
    blob, tokens, edition_date, date_source, location_raw, guessed_title = _date_and_tokens(
        filename=filename, caption=caption, message_date=message_date
    )

    for raw in _alias_lookup_keys(guessed_title=guessed_title, blob=blob, tokens=tokens):
        if newspaper_repo.resolve_alias(db, raw):
            return _finish_parse(
                db,
                filename=filename,
                caption=caption,
                edition_date=edition_date,
                date_source=date_source,
                location_raw=location_raw,
                guessed_title=guessed_title,
                blob=blob,
                tokens=tokens,
                llm_identity=None,
                allow_llm=False,
            )

    llm_identity = None
    from app.eta.llm_concurrency import llm_slot_async

    async with llm_slot_async():
        llm_identity = await _llm_identify_paper_async(
            db, filename=filename, caption=caption, tokens=tokens, blob=blob
        )
    return _finish_parse(
        db,
        filename=filename,
        caption=caption,
        edition_date=edition_date,
        date_source=date_source,
        location_raw=location_raw,
        guessed_title=guessed_title,
        blob=blob,
        tokens=tokens,
        llm_identity=llm_identity,
        allow_llm=False,
    )
