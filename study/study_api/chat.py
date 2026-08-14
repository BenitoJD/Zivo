import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
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
from app.services.document_access import require_document
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.response_cache import get_cached_response, store_response
from app.services.rate_limit import rate_limit_dependency
from app.services.tutor_retrieval import compress_chat_history
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


def _history_digest(prior: list[dict]) -> str:
    """Fold prior user turns into the semantic-cache scope so follow-ups don't collide."""
    h = hashlib.sha256()
    for m in prior:
        if (m.get("role") or "") != "user":
            continue
        h.update((m.get("content") or "")[:500].encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


def _shrink_prior_messages(prior: list[dict]) -> list[dict]:
    """Compress older turns via Tutor Retrieval (cuts input tokens)."""
    return list(compress_chat_history(prior).messages)


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


_HIGHLIGHTED_RE = re.compile(
    r'I highlighted(?: this while studying)?:\s*\n\n"([^"]{1,240})"',
    re.IGNORECASE,
)


def _retrieval_query(message: str) -> str:
    """Prefer the quoted highlight over the full turn text for vector search."""
    m = _HIGHLIGHTED_RE.search(message or "")
    if m:
        return m.group(1).strip()
    text = (message or "").strip()
    return text[:500] if len(text) > 500 else text


def _is_prefetched_reference(scope: dict | None) -> bool:
    return (scope or {}).get("reference_source") in ("wikipedia", "dictionary")


_WIKIPEDIA_PREFETCH_RE = re.compile(
    r"Wikipedia summary of\s+\"",
    re.IGNORECASE,
)


def _message_has_prefetched_reference(message: str) -> bool:
    return bool(_WIKIPEDIA_PREFETCH_RE.search(message or ""))


_PREFETCHED_REFERENCE_DIRECTIVE = (
    "Reference lookup (this turn): The learner highlighted a term from their current "
    "study question. Use the Wikipedia summary in their message plus the Learn session "
    "block. Explain the highlighted term in that quiz context only. Do not discuss "
    "unrelated topics from other newspaper pages or earlier chat turns."
)


def _cache_eligible(
    *,
    include_image: bool,
    selection_text: str | None,
    has_citations: bool,
    doc_count: int,
    has_history: bool,
    conversational_followup: bool = False,
    prefetched_reference: bool = False,
) -> bool:
    """Whether a semantic cache lookup/store is safe for this turn.

    Cache matches by embedding similarity within a scope_hash that folds prior
    user turns (``history_digest``). Conversational follow-ups ("go deeper",
    "thanks") stay uncached — they need the live model. Standalone paraphrases
    after history remain eligible when the digest matches.
    """
    if include_image:
        return False
    if prefetched_reference:
        return False
    if selection_text:
        return False
    if not has_citations:
        return False
    if doc_count != 1:
        return False
    if has_history and conversational_followup:
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
    selected_choice_index: int | None = Field(default=None, ge=0, le=25)
    selected_choice_indices: list[int] | None = Field(default=None, max_length=26)
    confirmed_choice_index: int | None = Field(default=None, ge=0, le=25)
    confirmed_choice_indices: list[int] | None = Field(default=None, max_length=26)
    answer_correct: bool | None = None
    mentions: list[str] = Field(default_factory=list, max_length=20)
    # Client-side reference lookup already embedded in the message (Wikipedia, etc.).
    reference_source: str | None = Field(default=None, max_length=32)
    # Which study surface the chat is on. "read" → the buddy is about the document
    # being read, so Learn-mode page/question context must NOT be injected.
    mode: str | None = Field(default=None, max_length=16)

    @field_validator("mentions")
    @classmethod
    def cap_mention_length(cls, value: list[str]) -> list[str]:
        return [m[:200] for m in value]

    @field_validator("selected_choice_indices", "confirmed_choice_indices")
    @classmethod
    def cap_choice_indices(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        return [int(i) for i in value if 0 <= int(i) <= 25][:26]


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


_CHAT_SURFACES = {"read", "learn", "test", "brainstorm", "socratic"}


def _chat_surface(mode: str | None) -> str:
    """The conversation surface for a study mode — keeps Read / Learn / Test /
    Brainstorm chats fully separate; anything else shares a neutral 'general' thread.

    Mirrored by ``chatSurfaceForMode`` in frontend/lib/api/queries.ts — a mode added
    to one and not the other silently merges into 'general'."""
    m = (mode or "").strip().lower()
    return m if m in _CHAT_SURFACES else "general"


def _get_or_create_thread(
    db: Session,
    account_id: uuid.UUID | None,
    doc: Document,
    surface: str = "general",
    *,
    guest_id: str | None = None,
) -> ChatThread:
    artifact_id, captured_at = _artifact_ref(doc)
    # Guests share account_id=NULL. Without a guest key they would all land on one
    # thread for public/demo docs. Encode guest id into the surface so each guest
    # gets an isolated conversation without a schema migration.
    thread_surface = surface
    if account_id is None and guest_id:
        thread_surface = f"g:{guest_id}:{surface}"
    thread = (
        db.query(ChatThread)
        .filter(
            ChatThread.artifact_id == artifact_id,
            ChatThread.artifact_captured_at == captured_at,
            ChatThread.account_id == account_id,
            ChatThread.surface == thread_surface,
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
        surface=thread_surface,
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
                ChatThread.surface == thread_surface,
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
    thread = _get_or_create_thread(
        db, user.id if user else None, doc, _chat_surface(surface), guest_id=guest_id
    )
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
    current = _get_or_create_thread(
        db, user.id if user else None, doc, resolved_surface, guest_id=guest_id
    )
    new_version = current.version + 1
    artifact_id, captured_at = _artifact_ref(doc)
    # Keep the same guest-scoped surface key as _get_or_create_thread.
    thread_surface = current.surface
    thread = ChatThread(
        account_id=user.id if user else None,
        artifact_id=artifact_id,
        artifact_captured_at=captured_at,
        surface=thread_surface,
        version=new_version,
    )
    db.add(thread)
    db.commit()
    return {"version": new_version}


class PersistChatMcqsIn(BaseModel):
    """One or more ``zv-mcq`` blocks the assistant produced in chat."""

    questions: list[dict[str, Any]]
    page_number: int = 1
    surface: str | None = None


@router.post("/mcqs/persist", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
def persist_chat_mcqs(
    body: PersistChatMcqsIn,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict[str, int]:
    """Persist chat-produced MCQs into the Learn pool for a document.

    Called from the tutor UI's "Add these to Learn" action: the assistant emits
    ``zv-mcq`` blocks when asked for practice questions; the frontend parses them
    and posts them here. Reuses the generation pipeline's ``_persist_assertion``
    (facet, concepts, birth difficulty) so the questions behave exactly like
    cooked ones — they surface in the Learn queue and feed the engines.
    """
    require_document(db, document_id, user, guest_id)  # access check + 404
    questions = [q for q in (body.questions or []) if isinstance(q, dict) and str(q.get("question") or "").strip()]
    if not questions:
        raise HTTPException(status_code=422, detail="No valid questions provided")
    if len(questions) > 10:
        raise HTTPException(status_code=422, detail="At most 10 questions per request")

    from app.graphs.generation_graph import _persist_assertion

    page = max(1, int(body.page_number or 1))
    # Next 1-based sequence for this page's MCQ facet (mirrors the cook path).
    seq_row = db.execute(
        text(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
            FROM qb.mcq_assertion_facets
            WHERE artifact_id = :aid AND page_number = :page
            """
        ),
        {"aid": document_id, "page": page},
    ).scalar()
    sequence = int(seq_row or 1)
    persisted = 0
    for q in questions:
        options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        correct = q.get("correct_index")
        if len(options) < 2 or not isinstance(correct, int) or not (0 <= correct < len(options)):
            continue
        _persist_assertion(
            db,
            document_id,
            {
                "question": str(q["question"]).strip(),
                "options": options,
                "correct_index": correct,
                "explanation": str(q.get("explanation") or "").strip(),
            },
            page_number=page,
            sequence=sequence,
            serve_mode="learn",
        )
        sequence += 1
        persisted += 1
    db.commit()
    return {"persisted": persisted}


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
        thread = _get_or_create_thread(db, user.id if user else None, doc, surface, guest_id=guest_id)
        history = (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(_HISTORY_LIMIT)
            .all()
        )
        history = list(reversed(history))
        prior = [
            {"role": m.role, "content": m.content}
            for m in history
            if (m.content or "").strip()
            and not (m.role == "assistant" and _is_transient_assistant_error(m.content))
        ]
        prior = _shrink_prior_messages(prior)

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
                scope = dict(request_scope) if request_scope else {}
                mode = str(request_scope.get("mode") or "").lower()
                read_mode = mode == "read"
                brainstorm_mode = mode == "brainstorm"
                socratic_mode = mode == "socratic"
                scope["mode"] = "read" if read_mode else (request_scope.get("mode") or None)
                # Read-mode chat is about the document, not the Learn loop — keep its
                # page/question signal out of the scope entirely. Brainstorm is the
                # same: it ranges over the whole source, so pinning it to the Learn
                # queue's current question would collapse it back into tutoring.
                # Socratic is a dialogue about the same material — no Learn pinning,
                # so the questions follow the learner's answers, not the queue.
                if not read_mode and not brainstorm_mode and not socratic_mode:
                    scope.update(
                        learn_scope_fields(
                            stream_db,
                            doc.id,
                            doc,
                            request_scope=request_scope,
                            user=user,
                            guest_id=guest_id,
                        )
                    )
                scope = normalize_chat_scope(scope, doc)
                prefetched_ref = _is_prefetched_reference(scope)
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
                        user=user,
                        guest_id=guest_id,
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
                                query=_retrieval_query(request_message),
                                scope=scope,
                                mentions=scope.get("mentions") or [],
                                prior_messages=prior_messages,
                            )

                    def _learn_context_for_chat() -> str | None:
                        if brainstorm_mode or socratic_mode:
                            return None
                        with SessionLocal() as learn_db:
                            learn_doc = learn_db.get(Document, document_id)
                            if not learn_doc:
                                return None
                            return build_learn_chat_context(
                                learn_db,
                                learn_doc.id,
                                learn_doc,
                                scope=scope,
                                user=user,
                                guest_id=guest_id,
                            )

                    retrieved, learn_context = await asyncio.gather(
                        asyncio.to_thread(_retrieve_for_chat),
                        asyncio.to_thread(_learn_context_for_chat),
                    )
                citations = retrieved.get("citations", [])
                # Prefix-cache discipline (this is what makes provider prompt caching
                # hit): the leading [system, ...history] must be byte-stable across
                # turns. So the system prompt is never mutated per-turn, and the
                # retrieved context (which changes every turn) rides in the FINAL
                # user message — not as a mid-list message before the history.
                # Per-surface system prompt. Still byte-stable *within* a surface, so
                # prefix caching keeps hitting; brainstorm just gets a different prefix.
                system = get_prompt(
                    stream_db,
                    "socratic_system"
                    if socratic_mode
                    else ("brainstorm_system" if brainstorm_mode else "tutor_system"),
                )
                messages = [{"role": "system", "content": system}]
                # Wikipedia / dictionary lookups carry their own reference text. Prior
                # chat turns (e.g. another newspaper page) would steer answers wrong.
                model_history = (
                    []
                    if prefetched_ref
                    else (retrieved.get("messages") or prior_messages)
                )
                messages.extend(model_history)

                context_block = retrieved.get("context_block") or ""
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
                if prefetched_ref:
                    trailer_parts.append(_PREFETCHED_REFERENCE_DIRECTIVE)
                learn_lead = learn_context or ""
                has_history = bool(prior_messages)
                guardrail_applies = (
                    learn_context
                    and _learn_context_has_active_question(learn_context)
                    and not _user_requests_mcq_answer(request_message)
                    and not (has_history and is_conversational_followup(request_message))
                )
                if guardrail_applies:
                    trailer_parts.append(_MCQ_ANSWER_GUARDRAIL)
                if _looks_like_quiz(request_message):
                    trailer_parts.append(get_prompt(stream_db, "mcq_format"))
                if trailer_parts:
                    user_content = user_content + "\n\n" + "\n\n".join(trailer_parts)
                body_parts: list[str] = []
                if learn_lead:
                    body_parts.append(learn_lead)
                if context_block:
                    body_parts.append(
                        "Document excerpts (retrieved for this turn):\n\n" + context_block
                    )
                body_parts.append(user_content)
                if len(body_parts) == 1:
                    final_user_content = body_parts[0]
                else:
                    final_user_content = "\n\n---\n\n".join(body_parts)
                messages.append({"role": "user", "content": final_user_content})

                # Brainstorm never serves the semantic response cache. Two near-identical
                # prompts returning the identical stored answer is fine for a tutor and
                # fatal for ideation — the point is that asking again gives you new angles.
                cache_eligible = not brainstorm_mode and _cache_eligible(
                    include_image=include_image,
                    selection_text=scope.get("selection_text"),
                    has_citations=bool(citations),
                    doc_count=len(doc_ids),
                    has_history=has_history,
                    conversational_followup=is_conversational_followup(request_message),
                    prefetched_reference=prefetched_ref,
                )
                artifact_id, artifact_captured_at = _artifact_ref(doc)
                cached: dict | None = None
                query_embedding: list[float] | None = None
                cache_scope = dict(scope)
                if has_history:
                    cache_scope["history_digest"] = _history_digest(prior_messages)
                if cache_eligible:
                    query_embedding = await asyncio.to_thread(embed_query, request_message)
                    cached = await asyncio.to_thread(
                        get_cached_response,
                        stream_db,
                        document_id=artifact_id,
                        artifact_captured_at=artifact_captured_at,
                        scope=cache_scope,
                        query_embedding=query_embedding,
                    )
                elif has_history and is_conversational_followup(request_message):
                    logger.debug(
                        "chat cache skipped: conversational follow-up doc=%s",
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
                        scope=cache_scope,
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


@router.post(
    "/mcq/grade",
    response_model=McqGradeResponse,
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
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
        from app.services.tutor_retrieval import grade_context_top_n

        top_n = grade_context_top_n()
        context = "\n\n".join(c["text"] for c in chunks[:top_n])

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
