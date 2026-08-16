"""Wikidata + Wikipedia backbone for the practice library concept graph.

The taxonomy is Wikidata by reference: we never store the world's concepts, we
lazily materialize an intel.entity row (keyed `wikidata:<qid>`) only for the
concepts questions actually touch. Hierarchy comes from Wikidata's own
`subclass_of` (P279) edges.

All calls use httpx with a generous timeout and a descriptive User-Agent,
mirroring backend/app/services/web_import.py. These are read-only public APIs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.engine_runtime import pick
from app.services.http_client import zivo_http_client

# Wikidata property for "subclass of".
_SUBCLASS_OF_PROP = "P279"


class WikidataError(Exception):
    """Raised when Wikidata/Wikipedia lookups fail."""


@dataclass(frozen=True)
class ConceptHit:
    qid: str
    label: str
    description: str
    concept_uri: str


@dataclass(frozen=True)
class ConceptDetail:
    qid: str
    label: str
    description: str
    concept_uri: str
    parents: list[ConceptHit]  # direct superclasses (subclass_of)


@dataclass(frozen=True)
class WikipediaArticle:
    qid: str
    title: str
    text: str
    source_url: str


def _client() -> httpx.AsyncClient:
    return zivo_http_client()


async def search_concepts(query: str, limit: int = 10) -> list[ConceptHit]:
    """Search Wikidata for entities matching `query` (wbsearchentities)."""
    query = (query or "").strip()

    async def _search() -> list[ConceptHit]:
        settings = get_settings()
        params = {
            "action": "wbsearchentities",
            "search": query,
            "language": "en",
            "format": "json",
            "limit": str(max(1, min(limit, 50))),
            "type": "item",
        }
        try:
            async with _client() as client:
                resp = await client.get(settings.wikidata_api_base, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise WikidataError(f"Wikidata search failed: {exc}") from exc

        results: list[ConceptHit] = []
        for item in data.get("search", []):
            qid = item.get("id") or ""
            pick(
                qid.startswith("Q"),
                lambda: results.append(
                    ConceptHit(
                        qid=qid,
                        label=item.get("label") or qid,
                        description=item.get("description") or "",
                        concept_uri=item.get("concepturi")
                        or f"http://www.wikidata.org/entity/{qid}",
                    )
                ),
                lambda: None,
            )
        return results

    return await pick(not query, _empty_hits, _search)


async def _empty_hits() -> list[ConceptHit]:
    return []


async def get_concept(qid: str) -> ConceptDetail:
    """Resolve a single Wikidata item to its label, description, and parents."""
    qid = (qid or "").strip().upper()

    def _bad_qid() -> None:
        raise WikidataError(f"Invalid QID: {qid!r}")

    pick(not qid.startswith("Q"), _bad_qid, lambda: None)
    settings = get_settings()
    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "labels|descriptions|claims",
        "languages": "en",
        "format": "json",
    }
    try:
        async with _client() as client:
            resp = await client.get(settings.wikidata_api_base, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise WikidataError(f"Wikidata lookup failed: {exc}") from exc

    entities = data.get("entities", {})
    entity = entities.get(qid)

    def _missing() -> None:
        raise WikidataError(f"Concept {qid} not found")

    pick(not entity, _missing, lambda: None)

    labels = entity.get("labels", {})
    label = (labels.get("en") or {}).get("value") or qid
    descriptions = entity.get("descriptions", {})
    description = (descriptions.get("en") or {}).get("value") or ""

    parent_qids = _claim_qids(entity, _SUBCLASS_OF_PROP)
    parents = await asyncio.gather(
        *(_mini_concept(pid) for pid in parent_qids), return_exceptions=True
    )
    parent_hits: list[ConceptHit] = []
    for p in parents:
        pick(isinstance(p, ConceptHit), lambda: parent_hits.append(p), lambda: None)

    return ConceptDetail(
        qid=qid,
        label=label,
        description=description,
        concept_uri=f"http://www.wikidata.org/entity/{qid}",
        parents=parent_hits,
    )


async def _mini_concept(qid: str) -> ConceptHit:
    """Fetch just label + description for a QID (for hierarchy edges)."""
    settings = get_settings()
    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "labels|descriptions",
        "languages": "en",
        "format": "json",
    }
    async with _client() as client:
        resp = await client.get(settings.wikidata_api_base, params=params)
        resp.raise_for_status()
        data = resp.json()
    entity = (data.get("entities") or {}).get(qid, {})
    label = ((entity.get("labels") or {}).get("en") or {}).get("value") or qid
    description = ((entity.get("descriptions") or {}).get("en") or {}).get("value") or ""
    return ConceptHit(
        qid=qid,
        label=label,
        description=description,
        concept_uri=f"http://www.wikidata.org/entity/{qid}",
    )


def _claim_qids(entity: dict, prop: str) -> list[str]:
    """Extract the target QIDs of a wikibase-item claim (e.g. P279)."""
    out: list[str] = []
    for claim in (entity.get("claims") or {}).get(prop, []):
        mainsnak = claim.get("mainsnak") or {}
        datavalue = mainsnak.get("datavalue") or {}
        vid = (datavalue.get("value") or {}).get("id") or ""
        pick(
            datavalue.get("type") == "wikibase-entityid" and vid.startswith("Q"),
            lambda: out.append(vid),
            lambda: None,
        )
    return out


async def get_wikipedia_article(qid: str) -> WikipediaArticle:
    """Resolve a QID to its English Wikipedia article (title + extract).

    Uses Wikidata sitelinks to find the enwiki title, then the REST summary API
    for a clean extract. Falls back gracefully if there is no English article.
    """
    qid = (qid or "").strip().upper()

    def _bad_qid() -> None:
        raise WikidataError(f"Invalid QID: {qid!r}")

    pick(not qid.startswith("Q"), _bad_qid, lambda: None)
    settings = get_settings()

    title = await _resolve_enwiki_title(qid)

    def _no_title() -> None:
        raise WikidataError(f"No English Wikipedia article for {qid}")

    pick(not title, _no_title, lambda: None)

    summary_url = f"{settings.wikipedia_api_base.rstrip('/')}/page/summary/{_urlencode_title(title)}"
    try:
        async with _client() as client:
            resp = await client.get(summary_url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise WikidataError(f"Wikipedia summary failed: {exc}") from exc

    extract = data.get("extract") or ""

    def _empty() -> None:
        raise WikidataError(f"Empty Wikipedia extract for {title}")

    pick(not extract, _empty, lambda: None)

    content_urls = (data.get("content_urls") or {}).get("desktop") or {}
    source_url = content_urls.get("page") or data.get("content_url") or ""

    return WikipediaArticle(
        qid=qid,
        title=data.get("title") or title,
        text=extract,
        source_url=source_url,
    )


async def _resolve_enwiki_title(qid: str) -> str | None:
    settings = get_settings()
    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "sitelinks/urls",
        "sitefilter": "enwiki",
        "format": "json",
    }
    try:
        async with _client() as client:
            resp = await client.get(settings.wikidata_api_base, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise WikidataError(f"Wikidata sitelink lookup failed: {exc}") from exc
    entity = (data.get("entities") or {}).get(qid, {})
    sitelink = (entity.get("sitelinks") or {}).get("enwiki") or {}
    return sitelink.get("title") or None


def _urlencode_title(title: str) -> str:
    """Quote a Wikipedia title for a URL path, preserving spaces as underscores."""
    from urllib.parse import quote

    return quote(title.replace(" ", "_"), safe="")
