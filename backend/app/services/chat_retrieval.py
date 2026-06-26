"""Build retrieval chunk lists for scoped tutor chat."""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.embed import embed_query
from app.services.rerank import rerank_chunks
from app.services.retrieval import fetch_chunks_for_page_range, merge_chunks, search_chunks

_PAGE_REF_RE = re.compile(
    r"\b(?:on|at)\s+page\s+(\d+)(?:\s*[-–]\s*(\d+))?\b"
    r"|\bpages?\s+(\d+)\s*(?:to|through|and)\s*(\d+)\b"
    r"|\bpage\s+(\d+)\s*[-–]\s*(\d+)\b"
    r"|\bpage\s+(\d+)\b",
    re.IGNORECASE,
)

# Over-fetch then rerank down. Raw cosine top-k is noisy; retrieving 20 and
# keeping the top N after cross-encoder rerank both cuts context tokens and
# improves relevance. MCQ grading already slices [:4] so it benefits for free.
_FETCH_LIMIT = 20
_TOP_N = 4


def _chat_rerank_enabled() -> bool:
    settings = get_settings()
    return bool(settings.rerank_enabled and settings.rerank_chat_enabled)


def _finish_chunks(query: str, chunks: list[dict], *, top_n: int = _TOP_N) -> list[dict]:
    if len(chunks) <= top_n:
        return chunks
    if _chat_rerank_enabled():
        return rerank_chunks(query, chunks, top_n=top_n)
    return chunks[:top_n]


def _extract_page_range(query: str) -> tuple[int | None, int | None]:
    text = (query or "").strip()
    if not text:
        return None, None
    match = _PAGE_REF_RE.search(text)
    if not match:
        return None, None
    groups = [g for g in match.groups() if g is not None]
    start = int(groups[0])
    end = int(groups[1]) if len(groups) > 1 and groups[1] else start
    if start <= 0 or end <= 0:
        return None, None
    return min(start, end), max(start, end)


def retrieve_document_chunks(
    db: Session,
    *,
    document_ids: list[uuid.UUID],
    query: str,
    scope: dict | None,
) -> list[dict]:
    scope = scope or {}
    current_page = scope.get("current_page")

    # Learn mode: normalize_chat_scope widens page_start/page_end to the RAG window
    # (up to 6 pages). That disabled the old single-page fast path and forced every
    # turn through embed + pgvector + cross-encoder rerank. Pin retrieval to the
    # active page first; only fall back to the window when that page has no chunks.
    if current_page is not None:
        page = int(current_page)
        primary_chunks = fetch_chunks_for_page_range(
            db,
            document_ids=document_ids,
            page_start=page,
            page_end=page,
        )
        if primary_chunks:
            return _finish_chunks(query, primary_chunks)

    query_start, query_end = _extract_page_range(query)
    scope_start = scope.get("page_start")
    scope_end = scope.get("page_end")
    has_scope = scope_start is not None
    has_query_ref = query_start is not None

    page_start = query_start if has_query_ref else (int(scope_start) if has_scope else None)
    page_end = query_end if has_query_ref else (int(scope_end) if has_scope else None)
    if current_page is not None and page_start is None:
        page_start = int(current_page)
        page_end = page_start
    if page_start is not None and page_end is None:
        page_end = page_start

    pinned_single_page = (
        page_start is not None
        and page_end is not None
        and page_start == page_end
        and (has_scope or current_page is not None or has_query_ref)
    )

    page_chunks: list[dict] = []
    if page_start is not None:
        page_chunks = fetch_chunks_for_page_range(
            db,
            document_ids=document_ids,
            page_start=page_start,
            page_end=page_end or page_start,
        )

    if pinned_single_page and page_chunks:
        return _finish_chunks(query, page_chunks)

    if has_query_ref and page_chunks and page_start == page_end:
        return _finish_chunks(query, page_chunks)

    if has_query_ref and page_chunks:
        embedding = embed_query(query)
        vector_chunks = search_chunks(
            db,
            document_ids=document_ids,
            query_embedding=embedding,
            limit=_FETCH_LIMIT,
            page_start=page_start,
            page_end=page_end,
        )
        merged = merge_chunks(page_chunks, vector_chunks)
        return _finish_chunks(query, merged)

    if has_query_ref and not page_chunks:
        embedding = embed_query(query)
        chunks = search_chunks(
            db,
            document_ids=document_ids,
            query_embedding=embedding,
            limit=_FETCH_LIMIT,
            page_start=page_start,
            page_end=page_end,
        )
        return _finish_chunks(query, chunks)

    embedding = embed_query(query)
    chunks = search_chunks(
        db,
        document_ids=document_ids,
        query_embedding=embedding,
        limit=_FETCH_LIMIT,
        page_start=page_start,
        page_end=page_end,
    )
    return _finish_chunks(query, chunks)
