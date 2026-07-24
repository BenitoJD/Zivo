"""Build retrieval chunk lists for scoped tutor chat."""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.embed import embed_query
from app.services.retrieval import fetch_chunks_for_page_range, merge_chunks, search_chunks
from app.services.tutor_retrieval import (
    DEFAULT_FETCH_LIMIT,
    DEFAULT_TOP_N,
    decide_page_pin,
    finish_ranked_chunks,
)

_PAGE_REF_RE = re.compile(
    r"\b(?:on|at)\s+page\s+(\d+)(?:\s*[-–]\s*(\d+))?\b"
    r"|\bpages?\s+(\d+)\s*(?:to|through|and)\s*(\d+)\b"
    r"|\bpage\s+(\d+)\s*[-–]\s*(\d+)\b"
    r"|\bpage\s+(\d+)\b",
    re.IGNORECASE,
)

# Over-fetch then rank down via Tutor Retrieval Engine.
_FETCH_LIMIT = DEFAULT_FETCH_LIMIT
_TOP_N = DEFAULT_TOP_N


def _chat_rerank_enabled() -> bool:
    settings = get_settings()
    return bool(settings.rerank_enabled and settings.rerank_chat_enabled)


def _finish_chunks(query: str, chunks: list[dict], *, top_n: int = _TOP_N) -> list[dict]:
    verdict = finish_ranked_chunks(
        query,
        chunks,
        top_n=top_n,
        rerank_enabled=_chat_rerank_enabled(),
    )
    return list(verdict.chunks)


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
    pin = decide_page_pin(scope)

    # Learn mode: normalize_chat_scope widens page_start/page_end to the RAG window
    # (up to 6 pages). Tutor Retrieval pin prefers the active page first; only fall
    # back to the window when that page has no chunks.
    if pin.pin_current_first and pin.page is not None:
        page = pin.page
        primary_chunks = fetch_chunks_for_page_range(
            db,
            document_ids=document_ids,
            page_start=page,
            page_end=page,
        )
        if primary_chunks:
            return _finish_chunks(query, primary_chunks)

    # Only engine-validated page — never re-read raw scope after decide_page_pin rejects.
    current_page = pin.page

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
