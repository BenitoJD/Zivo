"""pgvector similarity search and page-aware chunk fetch."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings

settings = get_settings()

# In-process memo for page-range chunk fetches. Chunks are immutable once a
# document is indexed, so refill batches on the same page re-query identical
# rows every time. Bound at 512 pages (~most of a large textbook) and keyed by
# (document_id, page_start, page_end); invalidated by process restart, which is
# fine because re-indexing writes new chunk rows only on explicit re-ingest.
_PAGE_CHUNK_CACHE_MAX = 512
_page_chunk_cache: OrderedDict[tuple[str, int, int], list[dict]] = OrderedDict()
_page_chunk_cache_lock = threading.Lock()


def _row_to_chunk(row: dict, *, score: float = 1.0) -> dict:
    return {
        "chunk_id": str(row["id"]),
        "document_id": str(row["document_id"]),
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "text": row["text"],
        "score": score,
    }


def fetch_chunks_for_page_range(
    db: Session,
    *,
    document_ids: list[uuid.UUID],
    page_start: int,
    page_end: int,
    limit: int = 48,
) -> list[dict]:
    """Return all indexed chunks overlapping a page range (full page text, in order)."""
    if not document_ids:
        return []

    # Single-document page fetches are the refill-batch hot path and are
    # immutable post-index — memoize them. Multi-document fan-outs (search)
    # bypass the cache since they're rare and blow the key cardinality.
    cache_key: tuple[str, int, int] | None = None
    if len(document_ids) == 1 and limit >= 48:
        cache_key = (str(document_ids[0]), page_start, page_end)
        with _page_chunk_cache_lock:
            cached = _page_chunk_cache.get(cache_key)
            if cached is not None:
                _page_chunk_cache.move_to_end(cache_key)
                # Copy so callers can't mutate the cached entry.
                return [dict(c) for c in cached]

    sql = text(
        """
        SELECT dc.id, dc.document_id, dc.page_start, dc.page_end, dc.text
        FROM document_chunks dc
        WHERE dc.document_id = ANY(CAST(:doc_ids AS uuid[]))
          AND dc.page_end >= :page_start
          AND dc.page_start <= :page_end
        ORDER BY dc.document_id, dc.page_start, dc.page_end, dc.id
        LIMIT :limit
        """
    )
    rows = db.execute(
        sql,
        {
            "doc_ids": [str(d) for d in document_ids],
            "page_start": page_start,
            "page_end": page_end,
            "limit": limit,
        },
    ).mappings().all()
    out = [_row_to_chunk(dict(r)) for r in rows]
    if cache_key is not None:
        with _page_chunk_cache_lock:
            _page_chunk_cache[cache_key] = [dict(c) for c in out]
            _page_chunk_cache.move_to_end(cache_key)
            while len(_page_chunk_cache) > _PAGE_CHUNK_CACHE_MAX:
                _page_chunk_cache.popitem(last=False)
    return out


def merge_chunks(*lists: list[dict]) -> list[dict]:
    """Dedupe by chunk_id while preserving first-seen order."""
    seen: set[str] = set()
    merged: list[dict] = []
    for chunk_list in lists:
        for chunk in chunk_list:
            chunk_id = chunk.get("chunk_id")
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            merged.append(chunk)
    return merged


def search_chunks(
    db: Session,
    *,
    document_ids: list[uuid.UUID],
    query_embedding: list[float],
    limit: int = 8,
    page_start: int | None = None,
    page_end: int | None = None,
) -> list[dict]:
    if not document_ids:
        return []

    vec_literal = "[" + ",".join(str(x) for x in query_embedding) + "]"
    params: dict = {
        "doc_ids": [str(d) for d in document_ids],
        "limit": limit,
    }

    page_filter = ""
    if page_start is not None:
        page_filter += " AND dc.page_end >= :page_start"
        params["page_start"] = page_start
    if page_end is not None:
        page_filter += " AND dc.page_start <= :page_end"
        params["page_end"] = page_end

    sql = text(
        f"""
        SELECT dc.id, dc.document_id, dc.page_start, dc.page_end, dc.text,
               1 - (dc.embedding <=> CAST(:vec AS vector)) AS score
        FROM document_chunks dc
        WHERE dc.document_id = ANY(CAST(:doc_ids AS uuid[]))
          AND dc.embedding IS NOT NULL
          {page_filter}
        ORDER BY dc.embedding <=> CAST(:vec AS vector)
        LIMIT :limit
        """
    )
    params["vec"] = vec_literal
    rows = db.execute(sql, params).mappings().all()
    return [_row_to_chunk(dict(r), score=float(r["score"])) for r in rows]
