"""Build retrieval chunk lists for scoped tutor chat."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.embed import embed_query
from app.services.retrieval import fetch_chunks_for_page_range, merge_chunks, search_chunks
from app.services.tutor_retrieval import (
    DEFAULT_FETCH_LIMIT,
    DEFAULT_TOP_N,
    decide_page_pin,
    extract_query_page_range,
    finish_ranked_chunks,
    plan_chunk_retrieval,
)

# Over-fetch then rank down via Tutor Retrieval Engine.
_FETCH_LIMIT = DEFAULT_FETCH_LIMIT
_TOP_N = DEFAULT_TOP_N


def _extract_page_range(query: str) -> tuple[int | None, int | None]:
    return extract_query_page_range(query)


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


def retrieve_document_chunks(
    db: Session,
    *,
    document_ids: list[uuid.UUID],
    query: str,
    scope: dict | None,
) -> list[dict]:
    scope = scope or {}
    pin = decide_page_pin(scope)
    plan = plan_chunk_retrieval(pin, scope, query)

    if plan.strategy == "pin_page":
        primary_chunks = fetch_chunks_for_page_range(
            db,
            document_ids=document_ids,
            page_start=plan.page_start,
            page_end=plan.page_end,
        )
        if primary_chunks:
            return _finish_chunks(query, primary_chunks)
        plan = plan_chunk_retrieval(pin, scope, query, pin_missed=True)

    page_chunks: list[dict] = []
    if plan.page_start is not None:
        page_chunks = fetch_chunks_for_page_range(
            db,
            document_ids=document_ids,
            page_start=plan.page_start,
            page_end=plan.page_end or plan.page_start,
        )
        plan = plan_chunk_retrieval(
            pin,
            scope,
            query,
            pin_missed=True,
            page_chunk_count=len(page_chunks),
            page_chunks_fetched=True,
        )

    if plan.strategy == "page_range":
        return _finish_chunks(query, page_chunks)

    embedding = embed_query(query)
    vector_chunks = search_chunks(
        db,
        document_ids=document_ids,
        query_embedding=embedding,
        limit=_FETCH_LIMIT,
        page_start=plan.page_start,
        page_end=plan.page_end,
    )
    if plan.strategy == "page_plus_vector":
        return _finish_chunks(query, merge_chunks(page_chunks, vector_chunks))
    return _finish_chunks(query, vector_chunks)
