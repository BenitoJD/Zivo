"""LangGraph scoped-chat — retrieve_context → tutor_reply."""

from __future__ import annotations

import os
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.services.chat_retrieval import retrieve_document_chunks
from app.services.retrieval_gate import needs_retrieval

# Hard cap on the context_block chars assembled for the LLM. The MCQ generation
# path enforces the same kind of budget (generation_graph.CONTEXT_CHAR_BUDGET);
# chat previously joined every retrieved chunk's full text with no ceiling, so a
# page-scoped turn (up to 48 fetched chunks) could ship unbounded tokens. The
# citation list is unaffected — only the LLM-facing context_block is trimmed.
CHAT_CONTEXT_CHAR_BUDGET = int(os.getenv("ZIVO_CHAT_CONTEXT_CHAR_BUDGET", "6000"))


class ChatState(TypedDict, total=False):
    query: str
    scope: dict
    mentions: list[str]
    document_ids: list[str]
    messages: list[dict]
    retrieved_chunks: list[dict]
    citations: list[dict]
    answer: str


def _snippet_for_chunk(text: str, limit: int = 240) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def retrieve_context(state: ChatState, *, db: Session) -> dict[str, Any]:
    doc_ids = [uuid.UUID(d) for d in state.get("document_ids", [])]
    scope = state.get("scope") or {}
    query = state.get("query", "")
    chunks = retrieve_document_chunks(db, document_ids=doc_ids, query=query, scope=scope)
    if scope.get("selection_text"):
        chunks.insert(
            0,
            {
                "chunk_id": "selection",
                "document_id": str(doc_ids[0]) if doc_ids else "",
                "page_start": scope.get("page_start") or 1,
                "page_end": scope.get("page_end") or scope.get("page_start") or 1,
                "text": scope["selection_text"],
                "score": 1.0,
            },
        )
    citations = [
        {
            "document_id": c["document_id"],
            "snippet": _snippet_for_chunk(c.get("text", "")),
        }
        for c in chunks
    ]
    parts: list[str] = []
    total = 0
    for c in chunks:
        t = (c.get("text") or "").strip()
        if not t:
            continue
        if total + len(t) > CHAT_CONTEXT_CHAR_BUDGET and parts:
            break
        parts.append(t)
        total += len(t)
    context_block = "\n\n".join(parts)
    # Context is returned separately rather than injected as a mid-list system
    # message. The caller folds it into the final user turn so the leading
    # [system, ...history] prefix stays byte-stable across turns — which is
    # what makes provider prefix caching (MiMo/OpenAI) actually hit.
    messages = list(state.get("messages") or [])
    return {
        "retrieved_chunks": chunks,
        "citations": citations,
        "messages": messages,
        "context_block": context_block,
    }


def build_chat_graph():
    graph = StateGraph(ChatState)

    def retrieve_node(state: ChatState) -> dict[str, Any]:
        raise RuntimeError("Use run_retrieve() with db session")

    graph.add_node("retrieve_context", retrieve_node)
    graph.add_edge(START, "retrieve_context")
    graph.add_edge("retrieve_context", END)
    return graph.compile()


def run_retrieve(
    db: Session,
    *,
    document_ids: list[uuid.UUID],
    query: str,
    scope: dict,
    mentions: list[str],
    prior_messages: list[dict],
) -> dict[str, Any]:
    # Adaptive retrieval gate: short follow-ups / acknowledgements in an
    # ongoing conversation with no pinned page scope get no new retrieval.
    # We return empty citations/context and pass history through unchanged,
    # so the LLM continues from prior context without a pgvector query or
    # ~1,600 tokens of redundant chunks.
    if not needs_retrieval(
        query,
        scope=scope,
        has_history=bool(prior_messages),
    ):
        return {
            "retrieved_chunks": [],
            "citations": [],
            "messages": list(prior_messages),
            "context_block": "",
            "context_note": "",
        }
    state: ChatState = {
        "query": query,
        "scope": scope,
        "mentions": mentions,
        "document_ids": [str(d) for d in document_ids],
        "messages": prior_messages,
    }
    return retrieve_context(state, db=db)
