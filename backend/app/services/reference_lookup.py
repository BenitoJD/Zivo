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
from app.engine_runtime import pick
from app.services.content_worthiness import evaluate_reference_extract
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

    def _empty() -> None:
        raise ReferenceLookupError("Empty selection")

    pick(not cleaned, _empty, lambda: None)
    return cleaned.split()[0]


async def lookup_dictionary(text: str) -> DictionaryEntry:
    """Return the first definition for a word or short selection."""
    token = _dictionary_token(text)
    url = f"{_DICTIONARY_API}/{quote(token, safe='')}"
    try:
        async with _client() as client:
            resp = await client.get(url)

            def _not_found() -> None:
                raise ReferenceLookupError(f"No definition found for “{token}”")

            pick(resp.status_code == 404, _not_found, lambda: None)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ReferenceLookupError(f"Dictionary lookup failed: {exc}") from exc

    def _bad_data() -> None:
        raise ReferenceLookupError(f"No definition found for “{token}”")

    pick(not isinstance(data, list) or not data, _bad_data, lambda: None)

    entry = data[0]
    word = str(entry.get("word") or token)
    meanings = entry.get("meanings") or []

    def _from_meaning(meaning: dict) -> DictionaryEntry | None:
        defs = meaning.get("definitions") or []

        def _from_top() -> DictionaryEntry | None:
            top = defs[0]
            definition = (top.get("definition") or "").strip()
            example = (top.get("example") or "").strip() or None
            pos = (meaning.get("partOfSpeech") or "").strip() or None

            def _ok() -> DictionaryEntry:
                return DictionaryEntry(
                    word=word,
                    part_of_speech=pos,
                    definition=definition,
                    example=example,
                )

            return pick(bool(definition), _ok, lambda: None)

        return pick(bool(defs), _from_top, lambda: None)

    hits = list(filter(None, map(_from_meaning, meanings)))

    def _none() -> None:
        raise ReferenceLookupError(f"No definition found for “{token}”")

    pick(not hits, _none, lambda: None)
    return hits[0]


async def lookup_wikipedia_summary(query: str) -> WikipediaSummary:
    """Resolve free text to an English Wikipedia summary extract."""
    query = " ".join((query or "").split())

    def _empty() -> None:
        raise ReferenceLookupError("Empty query")

    pick(not query, _empty, lambda: None)

    title = await _resolve_wikipedia_title(query)

    async def _none_summary() -> WikipediaSummary | None:
        return None

    async def _from_title() -> WikipediaSummary | None:
        summary = await _fetch_wikipedia_summary(title)
        return pick(
            bool(summary and evaluate_reference_extract(summary.extract)),
            lambda: summary,
            lambda: None,
        )

    from_title = await pick(bool(title), _from_title, _none_summary)

    async def _return_title() -> WikipediaSummary:
        return from_title

    async def _wikidata() -> WikipediaSummary:
        from app.services import wikidata

        hits = await wikidata.search_concepts(query, limit=5)
        found: WikipediaSummary | None = None
        for hit in hits:
            async def _try_hit() -> None:
                nonlocal found
                try:
                    article = await wikidata.get_wikipedia_article(hit.qid)
                except wikidata.WikidataError:
                    return
                found = pick(
                    found is None and evaluate_reference_extract(article.text),
                    lambda: WikipediaSummary(
                        title=article.title,
                        extract=article.text,
                        source_url=article.source_url,
                    ),
                    lambda: found,
                )

            await pick(found is None, _try_hit, _none_summary)

        async def _from_found() -> WikipediaSummary:
            return found

        async def _title_fallback() -> WikipediaSummary:
            async def _fetch() -> WikipediaSummary | None:
                return await _fetch_wikipedia_summary(title)

            summary = await pick(bool(title), _fetch, _none_summary)

            def _missing() -> None:
                raise ReferenceLookupError(f"No Wikipedia article for “{query}”")

            pick(not summary, _missing, lambda: None)
            return summary

        return await pick(found is not None, _from_found, _title_fallback)

    return await pick(from_title is not None, _return_title, _wikidata)


async def _fetch_wikipedia_summary(title: str) -> WikipediaSummary | None:
    settings = get_settings()
    summary_url = (
        f"{settings.wikipedia_api_base.rstrip('/')}/page/summary/{_urlencode_title(title)}"
    )
    try:
        async with _client() as client:
            resp = await client.get(summary_url)

            async def _not_found() -> WikipediaSummary | None:
                return None

            async def _parse() -> WikipediaSummary | None:
                resp.raise_for_status()
                data = resp.json()
                extract = (data.get("extract") or "").strip()

                def _none() -> None:
                    return None

                def _ok() -> WikipediaSummary:
                    content_urls = (data.get("content_urls") or {}).get("desktop") or {}
                    source_url = content_urls.get("page") or data.get("content_url") or ""
                    return WikipediaSummary(
                        title=data.get("title") or title,
                        extract=extract,
                        source_url=source_url,
                    )

                return pick(not extract, _none, _ok)

            return await pick(resp.status_code == 404, _not_found, _parse)
    except httpx.HTTPError as exc:
        raise ReferenceLookupError(f"Wikipedia lookup failed: {exc}") from exc


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

    def _none() -> None:
        return None

    def _title() -> str | None:
        titles = data[1]
        return pick(not titles, _none, lambda: str(titles[0]))

    return pick(not isinstance(data, list) or len(data) < 2, _none, _title)


def _urlencode_title(title: str) -> str:
    return quote(title.replace(" ", "_"), safe="")
