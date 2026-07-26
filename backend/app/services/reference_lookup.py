"""Free public lookups for the selection toolbar: dictionary + Wikipedia.

Dictionary uses dictionaryapi.dev (no API key). Wikipedia uses the same REST
summary path as practice generation (wikidata.py), with opensearch to resolve
a free-text query to a title.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.services.http_client import zivo_http_client

_DICTIONARY_API = "https://api.dictionaryapi.dev/api/v2/entries/en"


class ReferenceLookupError(Exception):
    """Raised when a dictionary or Wikipedia lookup fails."""


@dataclass(frozen=True)
class DictionaryEntry:
    word: str
    part_of_speech: str | None
    definition: str
    example: str | None


@dataclass(frozen=True)
class WikipediaSummary:
    title: str
    extract: str
    source_url: str


def _client() -> httpx.AsyncClient:
    return zivo_http_client()


def _dictionary_token(text: str) -> str:
    """Pick one lookup token from a selection (first word when multi-word)."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        raise ReferenceLookupError("Empty selection")
    return cleaned.split()[0]


async def lookup_dictionary(text: str) -> DictionaryEntry:
    """Return the first definition for a word or short selection."""
    token = _dictionary_token(text)
    url = f"{_DICTIONARY_API}/{quote(token, safe='')}"
    try:
        async with _client() as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                raise ReferenceLookupError(f"No definition found for “{token}”")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ReferenceLookupError(f"Dictionary lookup failed: {exc}") from exc

    if not isinstance(data, list) or not data:
        raise ReferenceLookupError(f"No definition found for “{token}”")

    entry = data[0]
    word = str(entry.get("word") or token)
    meanings = entry.get("meanings") or []
    for meaning in meanings:
        defs = meaning.get("definitions") or []
        if not defs:
            continue
        top = defs[0]
        definition = (top.get("definition") or "").strip()
        if not definition:
            continue
        example = (top.get("example") or "").strip() or None
        pos = (meaning.get("partOfSpeech") or "").strip() or None
        return DictionaryEntry(
            word=word,
            part_of_speech=pos,
            definition=definition,
            example=example,
        )

    raise ReferenceLookupError(f"No definition found for “{token}”")


async def lookup_wikipedia_summary(query: str) -> WikipediaSummary:
    """Resolve free text to an English Wikipedia summary extract."""
    query = " ".join((query or "").split())
    if not query:
        raise ReferenceLookupError("Empty query")

    title = await _resolve_wikipedia_title(query)
    if title:
        summary = await _fetch_wikipedia_summary(title)
        if summary and _summary_is_usable(summary.extract):
            return summary

    # Disambiguation or weak opensearch hit: try Wikidata entity search.
    from app.services import wikidata

    hits = await wikidata.search_concepts(query, limit=5)
    for hit in hits:
        try:
            article = await wikidata.get_wikipedia_article(hit.qid)
        except wikidata.WikidataError:
            continue
        if _summary_is_usable(article.text):
            return WikipediaSummary(
                title=article.title,
                extract=article.text,
                source_url=article.source_url,
            )

    if title:
        summary = await _fetch_wikipedia_summary(title)
        if summary:
            return summary

    raise ReferenceLookupError(f"No Wikipedia article for “{query}”")


def _summary_is_usable(extract: str) -> bool:
    text = (extract or "").strip()
    if len(text) < 40:
        return False
    lowered = text.lower()
    if "may refer to" in lowered or "can refer to" in lowered:
        return False
    return True


async def _fetch_wikipedia_summary(title: str) -> WikipediaSummary | None:
    settings = get_settings()
    summary_url = (
        f"{settings.wikipedia_api_base.rstrip('/')}/page/summary/{_urlencode_title(title)}"
    )
    try:
        async with _client() as client:
            resp = await client.get(summary_url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ReferenceLookupError(f"Wikipedia lookup failed: {exc}") from exc

    extract = (data.get("extract") or "").strip()
    if not extract:
        return None

    content_urls = (data.get("content_urls") or {}).get("desktop") or {}
    source_url = content_urls.get("page") or data.get("content_url") or ""

    return WikipediaSummary(
        title=data.get("title") or title,
        extract=extract,
        source_url=source_url,
    )


async def _resolve_wikipedia_title(query: str) -> str | None:
    """Best-match enwiki title via MediaWiki opensearch."""
    params = {
        "action": "opensearch",
        "search": query,
        "limit": "1",
        "namespace": "0",
        "format": "json",
    }
    wiki_search = "https://en.wikipedia.org/w/api.php"
    try:
        async with _client() as client:
            resp = await client.get(wiki_search, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ReferenceLookupError(f"Wikipedia search failed: {exc}") from exc

    if not isinstance(data, list) or len(data) < 2:
        return None
    titles = data[1]
    if not titles:
        return None
    return str(titles[0])


def _urlencode_title(title: str) -> str:
    return quote(title.replace(" ", "_"), safe="")
