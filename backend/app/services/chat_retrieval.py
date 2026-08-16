"""Build retrieval chunk lists for scoped tutor chat."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.engine_runtime import apply, pick
from app.services.embed import embed_query
from app.services.retrieval import fetch_chunks_for_page_range, merge_chunks, search_chunks
from app.services.tutor_retrieval import (
    DEFAULT_FETCH_LIMIT,
    DEFAULT_TOP_N,
    ChunkRetrievalPlan,
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

    def _vector(active: ChunkRetrievalPlan, page_chunks: list[dict]) -> list[dict]:
        embedding = embed_query(query)
        vector_chunks = search_chunks(
            db,
            document_ids=document_ids,
            query_embedding=embedding,
            limit=_FETCH_LIMIT,
            page_start=active.page_start,
            page_end=active.page_end,
        )
        return apply(
            active.strategy,
            {
                "page_plus_vector": lambda: _finish_chunks(query, merge_chunks(page_chunks, vector_chunks)),
                "vector_only": lambda: _finish_chunks(query, vector_chunks),
                "page_range": lambda: _finish_chunks(query, page_chunks),
                "pin_page": lambda: _finish_chunks(query, vector_chunks),
            },
        )

    def _with_pages(active: ChunkRetrievalPlan) -> list[dict]:
        def _fetched() -> tuple[list[dict], ChunkRetrievalPlan]:
            page_chunks = fetch_chunks_for_page_range(
                db,
                document_ids=document_ids,
                page_start=active.page_start,
                page_end=active.page_end or active.page_start,
            )
            next_plan = plan_chunk_retrieval(
                pin,
                scope,
                query,
                pin_missed=True,
                page_chunk_count=len(page_chunks),
                page_chunks_fetched=True,
            )
            return page_chunks, next_plan

        page_chunks, next_plan = pick(
            active.page_start is not None,
            _fetched,
            lambda: ([], active),
        )
        return pick(
            next_plan.strategy == "page_range",
            lambda: _finish_chunks(query, page_chunks),
            lambda: _vector(next_plan, page_chunks),
        )

    def _pin() -> list[dict]:
        primary_chunks = fetch_chunks_for_page_range(
            db,
            document_ids=document_ids,
            page_start=plan.page_start,
            page_end=plan.page_end,
        )
        return pick(
            bool(primary_chunks),
            lambda: _finish_chunks(query, primary_chunks),
            lambda: _with_pages(plan_chunk_retrieval(pin, scope, query, pin_missed=True)),
        )

    return pick(plan.strategy == "pin_page", _pin, lambda: _with_pages(plan))
