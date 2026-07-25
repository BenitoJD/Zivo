"""LangGraph scoped-chat — retrieve_context → tutor_reply."""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy.orm import Session

from app.services.chat_retrieval import retrieve_document_chunks
from app.services.tutor_retrieval import decide_retrieval, plan_brainstorm_sample
from app.services.token_budget import CHAT_INPUT_MAX_TOKENS, truncate_to_tokens

_CHAT_CONTEXT_MAX_TOKENS = CHAT_INPUT_MAX_TOKENS


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
    for c in chunks:
        parts.append(c["text"])
    context_block = truncate_to_tokens("\n\n".join(parts), _CHAT_CONTEXT_MAX_TOKENS)
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


def _brainstorm_context(db: Session, document_id: uuid.UUID) -> str:
    """Whole-source breadth instead of top-k depth — the Brainstorm context block.

    Ordinary chat retrieval answers "what does the source say here", so it fetches the
    few chunks nearest the query. Brainstorming needs the opposite: to connect chapter 2
    to chapter 9 it has to see the shape of the whole source, and top-k can only
    recombine the passage it just read. So we send the topic outline (already built for
    Explain) plus chunks sampled evenly across the document rather than the first N.
    """
    from app.services.chunks import load_document_chunk_texts
    from app.services.topics import load_outline

    parts: list[str] = []
    topics = (load_outline(db, document_id) or {}).get("topics") or []
    if topics:
        lines = "\n".join(
            f"- {t.get('title', '')}: {t.get('summary', '')}".rstrip(": ") for t in topics
        )
        parts.append(f"The full topic map of this source:\n{lines}")

    chunks = load_document_chunk_texts(db, document_id)
    if chunks:
        # Evenly spaced, so the sample spans beginning to end instead of stopping
        # wherever the token budget runs out (which would be the first chapter only).
        sample = plan_brainstorm_sample(chunks)
        parts.append("Passages sampled across the source:\n\n" + "\n\n".join(sample.texts))

    return truncate_to_tokens("\n\n".join(parts), _CHAT_CONTEXT_MAX_TOKENS)


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
    # Brainstorm mode uses kept-ideas context instead of vector RAG.
    gate = decide_retrieval(
        query,
        scope=scope,
        has_history=bool(prior_messages),
    )
    if not gate.retrieve:
        if gate.reason == "brainstorm" and document_ids:
            return {
                "retrieved_chunks": [],
                "citations": [],
                "messages": list(prior_messages),
                "context_block": _brainstorm_context(db, document_ids[0]),
                "context_note": "",
            }
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
