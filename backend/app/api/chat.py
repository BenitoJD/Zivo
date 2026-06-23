import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db import SessionLocal, get_db
from app.graphs.chat_graph import run_retrieve
from app.graphs.mcq_graph import grade_mcq_answer, try_grade_mcq_fast
from app.models import Account, ChatMessage, ChatThread, Document, User
from app.schemas.mcq import McqGradeRequest, McqGradeResponse
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.chat_scope import normalize_chat_scope
from app.services.learn_chat_context import build_learn_chat_context, learn_scope_fields
from app.services.embed import embed_query
from app.services.llm_router import stream_chat_completion
from app.services.prompts import get_prompt
from app.services.guest import can_access_document
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.response_cache import get_cached_response, store_response
from app.services.usage import check_message_allowed, increment_message_count
from app.services.vision import build_user_message, is_image_document

logger = logging.getLogger(__name__)

router = APIRouter()

# Recent turns kept in the LLM prefix. Lowering from 20 → 6 shrinks the
# (cacheable) prefix and the per-turn input-token cost; recent context
# dominates answer quality in a tutor chat.
_HISTORY_LIMIT = 6

# Matches messages that look like a quiz/test request. Only when this hits do
# we inject the ~300-token mcq_format prompt — otherwise it's dead weight on
# every turn and destabilizes the provider prefix cache.
_QUIZ_RE = re.compile(
    r"\b(quiz|quizzes|mcq|mcqs|multiple[- ]choice|practice question|test me|"
    r"give me .* question|exam questions?)\b",
    re.IGNORECASE,
)


def _looks_like_quiz(message: str) -> bool:
    return bool(_QUIZ_RE.search(message or ""))


def _stream_error_event(message: str) -> dict[str, str]:
    return {"event": "error", "data": json.dumps({"message": message})}


class ChatScope(BaseModel):
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    selection_text: str | None = Field(default=None, max_length=8000)
    current_page: int | None = Field(default=None, ge=1)
    mentions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("mentions")
    @classmethod
    def cap_mention_length(cls, value: list[str]) -> list[str]:
        return [m[:200] for m in value]


class ChatRequest(BaseModel):
    document_id: uuid.UUID
    message: str = Field(min_length=1, max_length=4000)
    scope: ChatScope = Field(default_factory=ChatScope)
    use_vision: bool = False
    model_id: uuid.UUID | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: dict | None = None

    model_config = {"from_attributes": True}


def _resolve_document_ids(
    db: Session,
    primary: uuid.UUID,
    mentions: list[str],
    user: Account | None,
    guest_id: str | None,
) -> list[uuid.UUID]:
    """Map `@slug` mentions to document ids the caller can access."""
    if not mentions:
        return [primary]
    slugs = []
    seen = set()
    for raw in mentions:
        slug = raw.lstrip("@").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
    if not slugs:
        return [primary]
    q = db.query(Document).filter(Document.slug.in_(slugs))
    q = q.filter(Document.account_id.is_(None)) if not user else q.filter(
        (Document.account_id == user.id) | (Document.account_id.is_(None))
    )
    docs = q.all()
    ids = [primary]
    for doc in docs:
        if doc.id == primary:
            continue
        if can_access_document(doc, user, guest_id) and doc.id not in ids:
            ids.append(doc.id)
    return ids


def _artifact_ref(doc: Document) -> tuple[uuid.UUID, datetime]:
    return doc.artifact_id or doc.id, doc.artifact_captured_at or doc.created_at


def _get_or_create_thread(db: Session, account_id: uuid.UUID | None, doc: Document) -> ChatThread:
    artifact_id, captured_at = _artifact_ref(doc)
    thread = (
        db.query(ChatThread)
        .filter(
            ChatThread.artifact_id == artifact_id,
            ChatThread.artifact_captured_at == captured_at,
            ChatThread.account_id == account_id,
        )
        .order_by(ChatThread.version.desc())
        .first()
    )
    if thread:
        return thread
    thread = ChatThread(
        account_id=account_id,
        artifact_id=artifact_id,
        artifact_captured_at=captured_at,
        version=1,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


@router.get("/threads/{document_id}/messages", response_model=list[MessageOut])
def list_messages(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> list[ChatMessage]:
    doc = db.get(Document, document_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Document not found")
    thread = _get_or_create_thread(db, user.id if user else None, doc)
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


@router.post("/threads/{document_id}/clear", dependencies=[Depends(require_csrf_or_guest)])
def clear_thread(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict[str, int]:
    doc = db.get(Document, document_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Document not found")
    current = _get_or_create_thread(db, user.id if user else None, doc)
    new_version = current.version + 1
    artifact_id, captured_at = _artifact_ref(doc)
    thread = ChatThread(
        account_id=user.id if user else None,
        artifact_id=artifact_id,
        artifact_captured_at=captured_at,
        version=new_version,
    )
    db.add(thread)
    db.commit()
    return {"version": new_version}


@router.post("", dependencies=[Depends(require_csrf_or_guest)])
async def chat_stream(
    body: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> EventSourceResponse:
    doc = db.get(Document, body.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status != "ready":
        raise HTTPException(status_code=409, detail="Document not ready")

    scope_preview = body.scope.model_dump() if body.scope else {}
    if scope_preview.get("current_page") is not None:
        from app.services.rag_window import is_rag_window_ready

        if not is_rag_window_ready(db, doc.id, doc):
            raise HTTPException(status_code=409, detail="Preparing chat context…")

    # Surface an explicit 404/503 before persisting the user turn so the
    # thread doesn't accumulate orphan user messages when the model is wrong.
    include_image = body.use_vision or is_image_document(doc)
    if body.model_id is not None:
        from app.services.llm_registry import resolve_chat_model

        try:
            resolve_chat_model(db, model_id=body.model_id, require_vision=include_image)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="No chat model available") from exc

    check_message_allowed(db, user=user, request=request, demo_cookie=guest_id)

    thread = _get_or_create_thread(db, user.id if user else None, doc)
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.asc())
        .limit(_HISTORY_LIMIT)
        .all()
    )
    prior = [
        {"role": m.role, "content": m.content}
        for m in history
        if (m.content or "").strip()
    ]

    db.add(ChatMessage(thread_id=thread.id, role="user", content=body.message))
    db.commit()

    doc_snapshot = doc
    thread_id = thread.id
    prior_messages = prior
    request_message = body.message
    request_model_id = body.model_id
    request_scope = body.scope.model_dump() if body.scope else {}

    async def event_generator() -> Any:
        # Request-scoped `db` may close before this generator finishes; use a
        # dedicated session for all post-stream persistence.
        stream_db = SessionLocal()
        try:
            try:
                scope = normalize_chat_scope(request_scope, doc_snapshot)
                scope.update(learn_scope_fields(stream_db, doc_snapshot.id, doc_snapshot))
                doc_ids = _resolve_document_ids(
                    stream_db,
                    doc_snapshot.id,
                    scope.get("mentions") or [],
                    user,
                    guest_id,
                )
                if is_image_document(doc_snapshot):
                    retrieved = {
                        "citations": [],
                        "messages": prior_messages,
                        "context_block": "",
                        "context_note": "",
                    }
                else:
                    retrieved = await asyncio.to_thread(
                        run_retrieve,
                        stream_db,
                        document_ids=doc_ids,
                        query=request_message,
                        scope=scope,
                        mentions=scope.get("mentions") or [],
                        prior_messages=prior_messages,
                    )
                citations = retrieved.get("citations", [])
                system = get_prompt(stream_db, "tutor_system")
                if _looks_like_quiz(request_message):
                    system = system + "\n\n" + get_prompt(stream_db, "mcq_format")
                messages = [{"role": "system", "content": system}]
                messages.extend(retrieved.get("messages") or prior_messages)

                context_block = retrieved.get("context_block") or ""
                user_content = build_user_message(
                    request_message,
                    doc_snapshot,
                    include_image=include_image,
                    current_page=scope.get("current_page"),
                    page_start=scope.get("page_start"),
                    page_end=scope.get("page_end"),
                )
                trailer_parts: list[str] = []
                learn_context = build_learn_chat_context(stream_db, doc_snapshot.id, doc_snapshot)
                if learn_context:
                    trailer_parts.append(learn_context)
                if context_block:
                    trailer_parts.append("Document excerpts:\n\n" + context_block)
                if trailer_parts:
                    user_content = user_content + "\n\n" + "\n\n".join(trailer_parts)
                messages.append({"role": "user", "content": user_content})

                cache_eligible = (
                    not include_image
                    and not scope.get("selection_text")
                    and bool(citations)
                    and len(doc_ids) == 1
                )
                artifact_id, artifact_captured_at = _artifact_ref(doc_snapshot)
                cached: dict | None = None
                query_embedding: list[float] | None = None
                if cache_eligible:
                    query_embedding = await asyncio.to_thread(embed_query, request_message)
                    cached = await asyncio.to_thread(
                        get_cached_response,
                        stream_db,
                        document_id=artifact_id,
                        artifact_captured_at=artifact_captured_at,
                        scope=scope,
                        query_embedding=query_embedding,
                    )
            except Exception as exc:
                logger.exception("chat setup failed: %s", exc)
                yield _stream_error_event("Tutor is busy. Try again.")
                return

            if cached:
                full = cached["response_text"]
                cache_citations = (cached.get("citations") or {}).get("sources", citations)
                try:
                    for i in range(0, len(full), 8):
                        yield {"event": "token", "data": json.dumps({"text": full[i : i + 8]})}
                    stream_db.add(
                        ChatMessage(
                            thread_id=thread_id,
                            role="assistant",
                            content=full,
                            citations={"sources": cache_citations},
                        )
                    )
                    stream_db.commit()
                    increment_message_count(
                        stream_db, user=user, request=request, demo_cookie=guest_id
                    )
                    yield {"event": "sources", "data": json.dumps({"citations": cache_citations})}
                    yield {"event": "done", "data": "{}"}
                except Exception as exc:
                    logger.exception("cached chat replay failed: %s", exc)
                    yield _stream_error_event("Tutor is busy. Try again.")
                return

            full = ""
            error_text: str | None = None
            try:
                async for token in stream_chat_completion(
                    messages,
                    stream_db,
                    model_id=request_model_id,
                    require_vision=include_image,
                ):
                    full += token
                    yield {"event": "token", "data": json.dumps({"text": token})}
                if not full.strip():
                    raise RuntimeError("empty model response")
                stream_db.add(
                    ChatMessage(
                        thread_id=thread_id,
                        role="assistant",
                        content=full,
                        citations={"sources": citations},
                    )
                )
                stream_db.commit()
                increment_message_count(
                    stream_db, user=user, request=request, demo_cookie=guest_id
                )
                if cache_eligible:
                    await asyncio.to_thread(
                        store_response,
                        stream_db,
                        document_id=artifact_id,
                        artifact_captured_at=artifact_captured_at,
                        scope=scope,
                        query_embedding=query_embedding,
                        response_text=full,
                        citations=citations,
                    )
                yield {"event": "sources", "data": json.dumps({"citations": citations})}
                yield {"event": "done", "data": "{}"}
            except Exception as exc:
                error_text = "Tutor is busy. Try again."
                logger.exception("chat stream failed: %s", exc)
                try:
                    stream_db.add(
                        ChatMessage(
                            thread_id=thread_id,
                            role="assistant",
                            content=error_text,
                            citations=None,
                        )
                    )
                    stream_db.commit()
                except Exception:
                    logger.exception("failed to persist assistant error message")
                yield _stream_error_event(error_text)
        finally:
            stream_db.close()

    return EventSourceResponse(event_generator())


@router.post("/mcq/grade", response_model=McqGradeResponse)
async def grade_mcq(
    body: McqGradeRequest,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> McqGradeResponse:
    doc = db.get(Document, body.document_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Document not found")
    if body.correct_index >= len(body.options) or body.selected_index >= len(body.options):
        raise HTTPException(status_code=400, detail="Invalid option index")

    fast = try_grade_mcq_fast(
        options=body.options,
        correct_index=body.correct_index,
        selected_index=body.selected_index,
        explanation=body.explanation,
    )
    if fast is not None:
        return McqGradeResponse(
            is_correct=bool(fast["is_correct"]),
            correct_index=body.correct_index,
            selected_index=body.selected_index,
            feedback=fast["feedback"],
        )

    context = ""
    if not is_image_document(doc):
        retrieved = await asyncio.to_thread(
            run_retrieve,
            db,
            document_ids=[doc.id],
            query=body.question,
            scope={},
            mentions=[],
            prior_messages=[],
        )
        chunks = retrieved.get("retrieved_chunks") or []
        context = "\n\n".join(c["text"] for c in chunks[:4])

    result = await grade_mcq_answer(
        db,
        question=body.question,
        options=body.options,
        correct_index=body.correct_index,
        selected_index=body.selected_index,
        explanation=body.explanation,
        document_context=context,
    )
    return McqGradeResponse(
        is_correct=result["is_correct"],
        correct_index=body.correct_index,
        selected_index=body.selected_index,
        feedback=result["feedback"],
    )
