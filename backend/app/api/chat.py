import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db import SessionLocal, get_db
from app.graphs.chat_graph import run_retrieve
from app.graphs.mcq_graph import grade_mcq_answer
from app.models import Account, ChatMessage, ChatThread, Document
from app.schemas.mcq import McqGradeRequest, McqGradeResponse
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.chat_scope import normalize_chat_scope
from app.services.retrieval_gate import is_conversational_followup
from app.services.learn_chat_context import build_learn_chat_context, learn_scope_fields
from app.services.embed import embed_query
from app.services.llm_router import is_failover_eligible, stream_chat_completion
from app.services.prompts import get_prompt
from app.services.guest import can_access_document
from app.api.access import require_document
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.response_cache import get_cached_response, store_response
from app.services.rate_limit import rate_limit_dependency
from app.services.usage import reserve_message_slot
from app.services.vision import build_user_message, is_image_document

logger = logging.getLogger(__name__)

router = APIRouter()

# Recent turns kept in the LLM prefix. Lowering from 20 → 6 shrinks the
# (cacheable) prefix and the per-turn input-token cost; recent context
# dominates answer quality in a tutor chat.
_HISTORY_LIMIT = 6
_LIST_MESSAGES_LIMIT = 100
_CHAT_BUSY_MESSAGE = "Tutor is busy. Try again."


def _user_facing_chat_error(exc: BaseException) -> str:
    if is_failover_eligible(exc):
        message = str(exc).lower()
        if "rate" in message or "429" in message or "too many requests" in message:
            return "Too many requests — wait a moment and try again."
        if "timeout" in message or "timed out" in message:
            return "That took too long — try a shorter question."
    return _CHAT_BUSY_MESSAGE

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


_MCQ_ANSWER_REQUEST_RE = re.compile(
    r"|".join(
        [
            r"\bwhat(?:'s| is) the (?:correct )?answer\b",
            r"\bwhich (?:option|choice) (?:is )?(?:correct|right)\b",
            r"\btell me (?:the )?answer\b",
            r"\bgive me (?:the )?answer\b",
            r"\bwhat should i (?:pick|choose|select)\b",
            r"\b(?:is|was) (?:option )?[a-d] (?:correct|right)\b",
            r"\bthe (?:correct|right) (?:option|choice|letter)\b",
            r"\breveal the answer\b",
        ]
    ),
    re.IGNORECASE,
)

_MCQ_ANSWER_GUARDRAIL = (
    "Tutor guardrail (this turn): The learner has not asked for the quiz answer. "
    "Explain the topic clearly. Do NOT state which option letter is correct, "
    'do NOT say "the correct answer is …", and do NOT map your explanation to A/B/C/D.'
)


def _user_requests_mcq_answer(message: str) -> bool:
    return bool(_MCQ_ANSWER_REQUEST_RE.search(message or ""))


def _learn_context_has_active_question(learn_context: str | None) -> bool:
    return bool(learn_context and "Current question stem:" in learn_context)


def _cache_eligible(
    *,
    include_image: bool,
    selection_text: str | None,
    has_citations: bool,
    doc_count: int,
    has_history: bool,
) -> bool:
    """Whether a semantic cache lookup/store is safe for this turn.

    The cache matches a new message to a stored reply by embedding similarity
    alone — it has no awareness of conversation history. So a follow-up like
    "no, I meant the other thing" or "go deeper" can be answered with the
    *original* cached reply, which is the main cause of the tutor "answering a
    different question". Restrict cache hits to fresh, standalone first turns
    with no prior history; once there's a back-and-forth, always call the model
    so replies track what the learner actually said.
    """
    if include_image:
        return False
    if selection_text:
        return False
    if not has_citations:
        return False
    if doc_count != 1:
        return False
    if has_history:
        return False
    return True


def _stream_error_event(message: str) -> dict[str, str]:
    return {"event": "error", "data": json.dumps({"message": message})}


def _is_transient_assistant_error(content: str | None) -> bool:
    return (content or "").strip() == _CHAT_BUSY_MESSAGE


class ChatScope(BaseModel):
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    selection_text: str | None = Field(default=None, max_length=8000)
    current_page: int | None = Field(default=None, ge=1)
    current_assertion_id: uuid.UUID | None = None
    confirmed_choice_index: int | None = Field(default=None, ge=0, le=25)
    answer_correct: bool | None = None
    mentions: list[str] = Field(default_factory=list, max_length=20)
    # Which study surface the chat is on. "read" → the buddy is about the document
    # being read, so Learn-mode page/question context must NOT be injected.
    mode: str | None = Field(default=None, max_length=16)

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


_CHAT_SURFACES = {"read", "learn", "test"}


def _chat_surface(mode: str | None) -> str:
    """The conversation surface for a study mode — keeps Read / Learn / Test chats
    fully separate; anything else shares a neutral 'general' thread."""
    m = (mode or "").strip().lower()
    return m if m in _CHAT_SURFACES else "general"


def _get_or_create_thread(
    db: Session, account_id: uuid.UUID | None, doc: Document, surface: str = "general"
) -> ChatThread:
    artifact_id, captured_at = _artifact_ref(doc)
    thread = (
        db.query(ChatThread)
        .filter(
            ChatThread.artifact_id == artifact_id,
            ChatThread.artifact_captured_at == captured_at,
            ChatThread.account_id == account_id,
            ChatThread.surface == surface,
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
        surface=surface,
        version=1,
    )
    try:
        db.add(thread)
        db.commit()
        db.refresh(thread)
        return thread
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(ChatThread)
            .filter(
                ChatThread.artifact_id == artifact_id,
                ChatThread.artifact_captured_at == captured_at,
                ChatThread.account_id == account_id,
                ChatThread.surface == surface,
                ChatThread.version == 1,
            )
            .first()
        )
        if existing:
            return existing
        raise


@router.get("/threads/{document_id}/messages", response_model=list[MessageOut])
def list_messages(
    document_id: uuid.UUID,
    offset: int = 0,
    surface: str | None = None,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> list[ChatMessage]:
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    doc = require_document(db, document_id, user, guest_id)
    thread = _get_or_create_thread(db, user.id if user else None, doc, _chat_surface(surface))
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.asc())
        .offset(offset)
        .limit(_LIST_MESSAGES_LIMIT)
        .all()
    )


@router.post("/threads/{document_id}/clear", dependencies=[Depends(require_csrf_or_guest)])
def clear_thread(
    document_id: uuid.UUID,
    surface: str | None = None,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict[str, int]:
    doc = require_document(db, document_id, user, guest_id)
    resolved_surface = _chat_surface(surface)
    current = _get_or_create_thread(db, user.id if user else None, doc, resolved_surface)
    new_version = current.version + 1
    artifact_id, captured_at = _artifact_ref(doc)
    thread = ChatThread(
        account_id=user.id if user else None,
        artifact_id=artifact_id,
        artifact_captured_at=captured_at,
        surface=resolved_surface,
        version=new_version,
    )
    db.add(thread)
    db.commit()
    return {"version": new_version}


def _prepare_chat_stream(
    body: ChatRequest,
    request: Request,
    user: Account | None,
    guest_id: str | None,
) -> dict[str, Any]:
    """Sync DB work for chat stream setup — run via asyncio.to_thread."""
    with SessionLocal() as db:
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

        include_image = body.use_vision or is_image_document(doc)
        if body.model_id is not None:
            from app.services.llm_registry import resolve_chat_model

            try:
                resolve_chat_model(db, model_id=body.model_id, require_vision=include_image)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=503, detail="No chat model available") from exc

        reserve_message_slot(db, user=user, request=request, demo_cookie=guest_id)

        surface = _chat_surface(scope_preview.get("mode"))
        thread = _get_or_create_thread(db, user.id if user else None, doc, surface)
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
            and not (m.role == "assistant" and _is_transient_assistant_error(m.content))
        ]

        db.add(ChatMessage(thread_id=thread.id, role="user", content=body.message))
        db.commit()

        return {
            "document_id": doc.id,
            "thread_id": thread.id,
            "prior_messages": prior,
            "request_message": body.message,
            "request_model_id": body.model_id,
            "request_scope": body.scope.model_dump() if body.scope else {},
            "include_image": include_image,
        }


@router.post("", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def chat_stream(
    body: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> EventSourceResponse:
    db.close()
    setup = await asyncio.to_thread(_prepare_chat_stream, body, request, user, guest_id)
    document_id = setup["document_id"]
    thread_id = setup["thread_id"]
    prior_messages = setup["prior_messages"]
    request_message = setup["request_message"]
    request_model_id = setup["request_model_id"]
    request_scope = setup["request_scope"]
    include_image = setup["include_image"]

    async def event_generator() -> Any:
        # Request-scoped `db` may close before this generator finishes; use a
        # dedicated session for all post-stream persistence.
        stream_db = SessionLocal()
        try:
            yield {"event": "status", "data": json.dumps({"phase": "thinking"})}
            try:
                doc = stream_db.get(Document, document_id)
                if not doc or not can_access_document(doc, user, guest_id):
                    yield _stream_error_event("Document not found.")
                    return
                scope = normalize_chat_scope(request_scope, doc)
                read_mode = str(request_scope.get("mode") or "").lower() == "read"
                scope["mode"] = "read" if read_mode else (request_scope.get("mode") or None)
                # Read-mode chat is about the document, not the Learn loop — keep its
                # page/question signal out of the scope entirely.
                if not read_mode:
                    scope.update(
                        learn_scope_fields(
                            stream_db,
                            doc.id,
                            doc,
                            request_scope=request_scope,
                        )
                    )
                doc_ids = _resolve_document_ids(
                    stream_db,
                    doc.id,
                    scope.get("mentions") or [],
                    user,
                    guest_id,
                )
                if is_image_document(doc):
                    retrieved = {
                        "citations": [],
                        "messages": prior_messages,
                        "context_block": "",
                        "context_note": "",
                    }
                    learn_context = build_learn_chat_context(
                        stream_db,
                        doc.id,
                        doc,
                        scope=scope,
                    )
                else:
                    yield {"event": "status", "data": json.dumps({"phase": "retrieving"})}

                    def _retrieve_for_chat() -> dict:
                        with SessionLocal() as retrieve_db:
                            retrieve_doc = retrieve_db.get(Document, document_id)
                            if not retrieve_doc:
                                raise HTTPException(status_code=404, detail="Document not found")
                            return run_retrieve(
                                retrieve_db,
                                document_ids=doc_ids,
                                query=request_message,
                                scope=scope,
                                mentions=scope.get("mentions") or [],
                                prior_messages=prior_messages,
                            )

                    def _learn_context_for_chat() -> str | None:
                        with SessionLocal() as learn_db:
                            learn_doc = learn_db.get(Document, document_id)
                            if not learn_doc:
                                return None
                            return build_learn_chat_context(
                                learn_db,
                                learn_doc.id,
                                learn_doc,
                                scope=scope,
                            )

                    retrieved, learn_context = await asyncio.gather(
                        asyncio.to_thread(_retrieve_for_chat),
                        asyncio.to_thread(_learn_context_for_chat),
                    )
                citations = retrieved.get("citations", [])
                system = get_prompt(stream_db, "tutor_system")
                if _looks_like_quiz(request_message):
                    system = system + "\n\n" + get_prompt(stream_db, "mcq_format")
                messages = [{"role": "system", "content": system}]
                context_block = retrieved.get("context_block") or ""
                if context_block:
                    messages.append(
                        {
                            "role": "user",
                            "content": "Document excerpts (pinned for this session):\n\n" + context_block,
                        }
                    )
                messages.extend(retrieved.get("messages") or prior_messages)

                user_content = build_user_message(
                    request_message,
                    doc,
                    include_image=include_image,
                    current_page=scope.get("current_page"),
                    page_start=scope.get("page_start"),
                    page_end=scope.get("page_end"),
                    db=stream_db,
                )
                trailer_parts: list[str] = []
                if learn_context:
                    trailer_parts.append(learn_context)
                has_history = bool(prior_messages)
                # The quiz answer guardrail stops the tutor from revealing the
                # correct option. But on a purely conversational follow-up
                # ("thanks", "go on", "can you rephrase that") re-appending it
                # biases the model to re-lecture from excerpts instead of just
                # responding. Keep it only when the turn is a real content
                # question that could otherwise leak the answer.
                guardrail_applies = (
                    learn_context
                    and _learn_context_has_active_question(learn_context)
                    and not _user_requests_mcq_answer(request_message)
                    and not (has_history and is_conversational_followup(request_message))
                )
                if guardrail_applies:
                    trailer_parts.append(_MCQ_ANSWER_GUARDRAIL)
                if trailer_parts:
                    user_content = user_content + "\n\n" + "\n\n".join(trailer_parts)
                messages.append({"role": "user", "content": user_content})

                cache_eligible = _cache_eligible(
                    include_image=include_image,
                    selection_text=scope.get("selection_text"),
                    has_citations=bool(citations),
                    doc_count=len(doc_ids),
                    has_history=has_history,
                )
                artifact_id, artifact_captured_at = _artifact_ref(doc)
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
                elif has_history:
                    logger.debug(
                        "chat cache skipped: follow-up turn (has_history) doc=%s",
                        document_id,
                    )
            except Exception as exc:
                logger.exception("chat setup failed: %s", exc)
                yield _stream_error_event(_user_facing_chat_error(exc))
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
                    yield {"event": "sources", "data": json.dumps({"citations": cache_citations})}
                    yield {"event": "done", "data": "{}"}
                except Exception as exc:
                    logger.exception("cached chat replay failed: %s", exc)
                    yield _stream_error_event(_CHAT_BUSY_MESSAGE)
                return

            full = ""
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
                logger.exception("chat stream failed: %s", exc)
                yield _stream_error_event(_user_facing_chat_error(exc))
        finally:
            stream_db.close()

    # Explicit anti-buffering headers so tokens stream through reverse proxies
    # (nginx/ingress) instead of arriving all at once.
    return EventSourceResponse(
        event_generator(),
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
        },
    )


@router.post("/mcq/grade", response_model=McqGradeResponse, dependencies=[Depends(rate_limit_dependency)])
async def grade_mcq(
    body: McqGradeRequest,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> McqGradeResponse:
    db.close()

    def _load_doc() -> Document:
        with SessionLocal() as session:
            return require_document(session, body.document_id, user, guest_id)

    doc = await asyncio.to_thread(_load_doc)
    if body.correct_index >= len(body.options) or body.selected_index >= len(body.options):
        raise HTTPException(status_code=400, detail="Invalid option index")

    # Stored explanation usually grounds grading; skip retrieval unless missing.
    context = ""
    if not (body.explanation or "").strip() and not is_image_document(doc):
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
