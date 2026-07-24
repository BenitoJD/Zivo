"""Adaptive newspaper PDF naming — aliases first, then LLM, then heuristic.

Filenames and captions change over time. Never hardcode paper abbreviations
(TH/ET/…). Learn mappings into ``qb.newspaper_paper_alias`` after LLM classifies.
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
_LLM_MIN_CONFIDENCE = 0.55

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


def _alias_lookup_keys(*, guessed_title: str, blob: str, tokens: list[str]) -> list[str]:
    keys = [guessed_title, blob]
    if tokens:
        keys.append(tokens[0])
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        norm = re.sub(r"[^a-z0-9]+", "", (k or "").lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(k)
    return out


def _match_known_brand(db: Session, paper_title: str) -> tuple[str, str]:
    """Map LLM title onto an existing brand when possible; else slugify."""
    title = re.sub(r"\s+", " ", (paper_title or "").strip()) or "Newspaper"
    slug, canon = newspaper_repo.make_paper_identity(title)
    title_l = title.lower()
    for b in newspaper_repo.list_brands(db):
        b_title = str(b.get("paper_title") or "")
        b_slug = str(b.get("paper_slug") or "")
        if b_slug == slug or b_title.lower() == title_l:
            return b_slug, b_title or canon
        if title_l in b_title.lower() or b_title.lower() in title_l:
            if len(b_title) >= 4:
                return b_slug, b_title
    return slug, canon


def _learn_aliases(
    db: Session,
    *,
    paper_slug: str,
    paper_title: str,
    guessed_title: str,
    tokens: list[str],
) -> None:
    """Cache learned mappings so the next identical naming pattern skips LLM."""
    keys = [guessed_title]
    if tokens:
        keys.append(tokens[0])
    for key in keys:
        if not (key or "").strip():
            continue
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
) -> tuple[str, str] | None:
    """Ask LLM what newspaper this PDF is. No hardcoded abbreviation tables.

    Caller must hold the LLM slot (``complete_chat`` / ``run_coro_in_worker`` /
    ``llm_slot_async``).
    """
    from app.services.llm_registry import default_chat_model_id
    from app.services.llm_router import acomplete_chat

    system = (
        "You identify Indian / English newspaper brands from Telegram PDF filenames "
        "and captions. Filenames change often (abbreviations, cities, dates). "
        "Expand common abbreviations using world knowledge (e.g. brand initials). "
        "Never treat a city or date as the newspaper name. "
        "Prefer matching a known catalog brand when the file clearly refers to it. "
        "Reply with JSON only."
    )
    user = (
        f"Known brands in our catalog:\n{_known_brands_prompt(db)}\n\n"
        f"Filename: {filename or '(none)'}\n"
        f"Caption: {caption or '(none)'}\n\n"
        'Return JSON: {"paper_title":"<canonical English newspaper name>",'
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
    if not title or confidence < _LLM_MIN_CONFIDENCE:
        logger.info(
            "newspaper paper-id rejected title=%r confidence=%s",
            title,
            confidence,
        )
        return None
    return _match_known_brand(db, title)


def _llm_identify_paper_sync(
    db: Session,
    *,
    filename: str,
    caption: str,
) -> tuple[str, str] | None:
    """Sync path — ``run_coro_in_worker`` owns the LLM slot."""
    from app.services.llm_sync import run_coro_in_worker

    try:
        return run_coro_in_worker(
            _llm_identify_paper_async(db, filename=filename, caption=caption)
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
    llm_identity: tuple[str, str] | None,
    allow_llm: bool,
) -> ParsedEdition:
    for raw in _alias_lookup_keys(guessed_title=guessed_title, blob=blob, tokens=tokens):
        alias = newspaper_repo.resolve_alias(db, raw)
        if alias:
            return ParsedEdition(
                paper_slug=alias[0],
                paper_title=alias[1],
                edition_date=edition_date,
                location_raw=location_raw,
                source=f"alias+{date_source}",
            )

    identity = llm_identity
    if identity is None and allow_llm:
        identity = _llm_identify_paper_sync(db, filename=filename, caption=caption)

    if identity:
        slug, title = identity
        _learn_aliases(
            db,
            paper_slug=slug,
            paper_title=title,
            guessed_title=guessed_title,
            tokens=tokens,
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
            db, filename=filename, caption=caption
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
