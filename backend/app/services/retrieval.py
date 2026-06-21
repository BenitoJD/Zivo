"""pgvector similarity search and page-aware chunk fetch."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings

settings = get_settings()


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
    return [_row_to_chunk(dict(r)) for r in rows]


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
