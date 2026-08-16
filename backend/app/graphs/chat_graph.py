"""LangGraph scoped-chat — retrieve_context → tutor_reply."""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.services.chat_retrieval import retrieve_document_chunks
from app.services.learn_chat_context import format_active_question_retrieval_chunk
from app.services.tutor_retrieval import decide_retrieval, plan_brainstorm_sample
from app.services.token_budget import CHAT_INPUT_MAX_TOKENS, truncate_to_tokens

_CHAT_CONTEXT_MAX_TOKENS = CHAT_INPUT_MAX_TOKENS

_RETRIEVE_DISPATCH = (
    Rule(when=(Pred("retrieve", "truthy"),), action="retrieve"),
    Rule(
        when=(Pred("reason", "eq", "brainstorm"), Pred("has_docs", "truthy")),
        action="brainstorm",
    ),
    Rule(when=(), action="skip"),
)


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
    return pick(
        len(cleaned) <= limit,
        lambda: cleaned,
        lambda: cleaned[: limit - 1].rstrip() + "…",
    )


def retrieve_context(state: ChatState, *, db: Session) -> dict[str, Any]:
    doc_ids = [uuid.UUID(d) for d in state.get("document_ids", [])]
    scope = state.get("scope") or {}
    query = state.get("query", "")
    chunks = retrieve_document_chunks(db, document_ids=doc_ids, query=query, scope=scope)
    assertion_id = scope.get("current_assertion_id")
    first_doc = pick(bool(doc_ids), lambda: str(doc_ids[0]), lambda: "")

    def _insert_active() -> None:
        page = scope.get("current_page")
        page_num = pick(page is not None, lambda: int(page), lambda: None)
        active_text = format_active_question_retrieval_chunk(
            db, str(assertion_id), page=page_num
        )

        def _pin() -> None:
            chunks.insert(
                0,
                {
                    "chunk_id": "active_question",
                    "document_id": first_doc,
                    "page_start": page_num or scope.get("page_start") or 1,
                    "page_end": page_num or scope.get("page_end") or scope.get("page_start") or 1,
                    "text": active_text,
                    "score": 1.0,
                },
            )

        pick(bool(active_text), _pin, lambda: None)

    pick(bool(assertion_id), _insert_active, lambda: None)

    def _insert_selection() -> None:
        chunks.insert(
            0,
            {
                "chunk_id": "selection",
                "document_id": first_doc,
                "page_start": scope.get("page_start") or 1,
                "page_end": scope.get("page_end") or scope.get("page_start") or 1,
                "text": scope["selection_text"],
                "score": 1.0,
            },
        )

    pick(bool(scope.get("selection_text")), _insert_selection, lambda: None)
    citations = [
        {
            "document_id": c["document_id"],
            "chunk_id": c.get("chunk_id"),
            "page_start": c.get("page_start"),
            "page_end": c.get("page_end"),
            "score": round(float(c.get("score") or 0), 3),
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
    # [system, ...history] prefix stays byte-stable across turns, which is
    # what makes provider prefix caching (MiMo/OpenAI) actually hit.
    messages = list(state.get("messages") or [])
    return {
        "retrieved_chunks": chunks,
        "citations": citations,
        "messages": messages,
        "context_block": context_block,
    }


def _brainstorm_context(db: Session, document_id: uuid.UUID) -> str:
    """Whole-source breadth instead of top-k depth: the Brainstorm context block.

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

    def _add_topics() -> None:
        lines = "\n".join(
            f"- {t.get('title', '')}: {t.get('summary', '')}".rstrip(": ") for t in topics
        )
        parts.append(f"The full topic map of this source:\n{lines}")

    pick(bool(topics), _add_topics, lambda: None)

    chunks = load_document_chunk_texts(db, document_id)

    def _add_sample() -> None:
        sample = plan_brainstorm_sample(chunks)
        parts.append("Passages sampled across the source:\n\n" + "\n\n".join(sample.texts))

    pick(bool(chunks), _add_sample, lambda: None)
    return truncate_to_tokens("\n\n".join(parts), _CHAT_CONTEXT_MAX_TOKENS)


def _empty_retrieve(prior_messages: list[dict], context_block: str = "") -> dict[str, Any]:
    return {
        "retrieved_chunks": [],
        "citations": [],
        "messages": list(prior_messages),
        "context_block": context_block,
        "context_note": "",
    }


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
    hit = first_match(
        _RETRIEVE_DISPATCH,
        {
            "retrieve": gate.retrieve,
            "reason": gate.reason,
            "has_docs": bool(document_ids),
        },
    )

    def _retrieve() -> dict[str, Any]:
        state: ChatState = {
            "query": query,
            "scope": scope,
            "mentions": mentions,
            "document_ids": [str(d) for d in document_ids],
            "messages": prior_messages,
        }
        return retrieve_context(state, db=db)

    return apply(
        hit.action,
        {
            "retrieve": _retrieve,
            "brainstorm": lambda: _empty_retrieve(
                prior_messages, _brainstorm_context(db, document_ids[0])
            ),
            "skip": lambda: _empty_retrieve(prior_messages),
        },
    )
