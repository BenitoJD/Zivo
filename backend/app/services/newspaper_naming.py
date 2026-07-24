"""Adaptive newspaper PDF naming — discover from live channel samples.

Filenames and captions change over time. Prefer aliases; fall back to heuristics
and Telegram message date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.repositories import newspaper as newspaper_repo

_IST = ZoneInfo("Asia/Kolkata")

# Common city tokens to strip when guessing paper title from a filename.
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
    source: str  # alias | heuristic | message_date


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
    for t in tokens:
        if t.lower() in _LOCATION_TOKENS and t.lower() not in {
            "epaper",
            "ebook",
            "premium",
            "english",
            "edition",
            "pdf",
        }:
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
    # Title-case joined tokens; short acronyms stay upper.
    parts = []
    for t in kept[:6]:
        parts.append(t.upper() if len(t) <= 3 else t.title())
    return " ".join(parts)


def parse_edition_meta(
    db: Session,
    *,
    filename: str,
    caption: str,
    message_date: datetime,
) -> ParsedEdition:
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

    # Try alias on full blob and on guessed title.
    for raw in (guessed_title, blob, tokens[0] if tokens else ""):
        alias = newspaper_repo.resolve_alias(db, raw)
        if alias:
            slug, title = alias
            return ParsedEdition(
                paper_slug=slug,
                paper_title=title,
                edition_date=edition_date,
                location_raw=location_raw,
                source=f"alias+{date_source}",
            )

    slug, title = newspaper_repo.make_paper_identity(guessed_title)
    # Learn this mapping so future naming drift can be corrected via alias table.
    newspaper_repo.upsert_alias(db, alias_key=guessed_title, paper_slug=slug, paper_title=title)
    return ParsedEdition(
        paper_slug=slug,
        paper_title=title,
        edition_date=edition_date,
        location_raw=location_raw,
        source=f"heuristic+{date_source}",
    )
