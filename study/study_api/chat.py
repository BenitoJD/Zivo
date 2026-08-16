import asyncio
import hashlib
import json
import logging
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
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
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
from app.services.http_outcome import evaluate_http_outcome
from app.services.presence import evaluate_presence
from app.services.tutor_retrieval import (
    CHAT_BUSY_MESSAGE,
    MCQ_ANSWER_GUARDRAIL,
    PREFETCHED_REFERENCE_DIRECTIVE,
    compress_chat_history,
    evaluate_chat_cache_eligible,
    is_prefetched_reference,
    learn_context_has_active_question,
    message_has_prefetched_reference,
    plan_chat_surface,
    plan_chat_system_prompt_key,
    plan_chat_thread_history,
    plan_chat_trailers,
    plan_chat_user_error,
    plan_retrieval_query,
    should_pin_learn_queue,
    user_requests_mcq_answer,
)
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.response_cache import get_cached_response, store_response
from app.services.rate_limit import rate_limit_dependency
from app.services.usage import reserve_message_slot
from app.services.vision import build_user_message, is_image_document

logger = logging.getLogger(__name__)

router = APIRouter()

_LIST_MESSAGES_LIMIT = 100
_CHAT_BUSY_MESSAGE = CHAT_BUSY_MESSAGE
_MCQ_ANSWER_GUARDRAIL = MCQ_ANSWER_GUARDRAIL
_PREFETCHED_REFERENCE_DIRECTIVE = PREFETCHED_REFERENCE_DIRECTIVE


def _raise(exc: BaseException) -> None:
    raise exc


def _raise_from(cause: BaseException, wrapped: BaseException) -> None:
    raise wrapped from cause


def _http(action: str, detail: str) -> None:
    _raise(HTTPException(status_code=evaluate_http_outcome(action).status, detail=detail))


def _account_id(user: Account | None):
    return pick(bool(user), lambda: user.id, lambda: None)


def _history_digest(prior: list[dict]) -> str:
    """Fold prior user turns into the semantic-cache scope so follow-ups don't collide."""
    h = hashlib.sha256()
    for m in filter(lambda row: (row.get("role") or "") == "user", prior):
        h.update((m.get("content") or "")[:500].encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


def _shrink_prior_messages(prior: list[dict]) -> list[dict]:
    """Compress older turns via Tutor Retrieval (cuts input tokens)."""
    return list(compress_chat_history(prior).messages)


def _user_facing_chat_error(exc: BaseException) -> str:
    return plan_chat_user_error(exc, failover_eligible=is_failover_eligible(exc))


def _looks_like_quiz(message: str) -> bool:
    from app.services.tutor_retrieval import looks_like_quiz

    return looks_like_quiz(message)


def _user_requests_mcq_answer(message: str) -> bool:
    return user_requests_mcq_answer(message)


def _learn_context_has_active_question(learn_context: str | None) -> bool:
    return learn_context_has_active_question(learn_context)


def _retrieval_query(message: str) -> str:
    return plan_retrieval_query(message)


def _is_prefetched_reference(scope: dict | None) -> bool:
    return is_prefetched_reference(scope)


def _message_has_prefetched_reference(message: str) -> bool:
    return message_has_prefetched_reference(message)


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

    Policy lives in Tutor Retrieval; this wrapper keeps the study chat call site
    and unit tests on a bool.
    """
    return evaluate_chat_cache_eligible(
        include_image=include_image,
        selection_text=selection_text,
        has_citations=has_citations,
        doc_count=doc_count,
        has_history=has_history,
        conversational_followup=conversational_followup,
        prefetched_reference=prefetched_reference,
    ).eligible


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
    reference_source: str | None = Field(default=None, max_length=32)
    mode: str | None = Field(default=None, max_length=16)

    @field_validator("mentions")
    @classmethod
    def cap_mention_length(cls, value: list[str]) -> list[str]:
        return [m[:200] for m in value]

    @field_validator("selected_choice_indices", "confirmed_choice_indices")
    @classmethod
    def cap_choice_indices(cls, value: list[int] | None) -> list[int] | None:
        return pick(
            value is None,
            lambda: None,
            lambda: list(filter(lambda i: 0 <= i <= 25, map(int, value)))[:26],
        )


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

    def _from_mentions() -> list[uuid.UUID]:
        slugs: list[str] = []
        seen: set[str] = set()
        for raw in mentions:
            slug = raw.lstrip("@").strip()
            pick(
                not slug or slug in seen,
                lambda: None,
                lambda: (seen.add(slug), slugs.append(slug)),
            )

        def _query() -> list[uuid.UUID]:
            q = db.query(Document).filter(Document.slug.in_(slugs))
            q = pick(
                not user,
                lambda: q.filter(Document.account_id.is_(None)),
                lambda: q.filter(
                    (Document.account_id == user.id) | (Document.account_id.is_(None))
                ),
            )
            docs = q.all()
            ids = [primary]
            for doc in docs:
                pick(
                    doc.id == primary
                    or not can_access_document(doc, user, guest_id)
                    or doc.id in ids,
                    lambda: None,
                    lambda: ids.append(doc.id),
                )
            return ids

        return pick(not slugs, lambda: [primary], _query)

    return pick(not mentions, lambda: [primary], _from_mentions)


def _artifact_ref(doc: Document) -> tuple[uuid.UUID, datetime]:
    return doc.artifact_id or doc.id, doc.artifact_captured_at or doc.created_at


def _chat_surface(mode: str | None) -> str:
    return plan_chat_surface(mode)


def _get_or_create_thread(
    db: Session,
    account_id: uuid.UUID | None,
    doc: Document,
    surface: str = "general",
    *,
    guest_id: str | None = None,
) -> ChatThread:
    artifact_id, captured_at = _artifact_ref(doc)
    thread_surface = pick(
        account_id is None and bool(guest_id),
        lambda: f"g:{guest_id}:{surface}",
        lambda: surface,
    )
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

    def _create() -> ChatThread:
        created = ChatThread(
            account_id=account_id,
            artifact_id=artifact_id,
            artifact_captured_at=captured_at,
            surface=thread_surface,
            version=1,
        )
        try:
            db.add(created)
            db.commit()
            db.refresh(created)
            return created
        except IntegrityError as err:
            caught = err
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
            return pick(bool(existing), lambda: existing, lambda: _raise(caught))

    return pick(bool(thread), lambda: thread, _create)


@router.get("/threads/{document_id}/messages", response_model=list[MessageOut])
def list_messages(
    document_id: uuid.UUID,
    offset: int = 0,
    surface: str | None = None,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> list[ChatMessage]:
    pick(
        offset < 0,
        lambda: _raise(HTTPException(status_code=400, detail="offset must be >= 0")),
        lambda: None,
    )
    doc = require_document(db, document_id, user, guest_id)
    thread = _get_or_create_thread(
        db, _account_id(user), doc, _chat_surface(surface), guest_id=guest_id
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
        db, _account_id(user), doc, resolved_surface, guest_id=guest_id
    )
    new_version = current.version + 1
    artifact_id, captured_at = _artifact_ref(doc)
    thread_surface = current.surface
    thread = ChatThread(
        account_id=_account_id(user),
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
    require_document(db, document_id, user, guest_id)
    questions = list(
        filter(
            lambda q: isinstance(q, dict) and str(q.get("question") or "").strip(),
            body.questions or [],
        )
    )
    apply(
        first_match(
            (
                Rule(when=(Pred("empty", "truthy"),), action="empty"),
                Rule(when=(Pred("too_many", "truthy"),), action="too_many"),
                Rule(when=(), action="ok"),
            ),
            {"empty": not questions, "too_many": len(questions) > 10},
        ).action,
        {
            "empty": lambda: _http("invalid", "No valid questions provided"),
            "too_many": lambda: _http("invalid", "At most 10 questions per request"),
            "ok": lambda: None,
        },
    )

    from app.graphs.generation_graph import _persist_assertion

    page = max(1, int(body.page_number or 1))
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
        options = list(filter(None, (str(o).strip() for o in (q.get("options") or []))))
        correct = q.get("correct_index")
        valid = (
            len(options) >= 2
            and isinstance(correct, int)
            and 0 <= correct < len(options)
        )

        def _save(item: dict, opts: list[str], idx: int, seq: int) -> None:
            _persist_assertion(
                db,
                document_id,
                {
                    "question": str(item["question"]).strip(),
                    "options": opts,
                    "correct_index": idx,
                    "explanation": str(item.get("explanation") or "").strip(),
                },
                page_number=page,
                sequence=seq,
                serve_mode="learn",
            )

        pick(valid, lambda: _save(q, options, correct, sequence), lambda: None)
        sequence += choose(valid, 1, 0)
        persisted += choose(valid, 1, 0)
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
        apply(
            first_match(
                (
                    Rule(when=(Pred("missing", "truthy"),), action="missing"),
                    Rule(when=(Pred("denied", "truthy"),), action="missing"),
                    Rule(when=(Pred("not_ready", "truthy"),), action="conflict"),
                    Rule(when=(), action="ok"),
                ),
                {
                    "missing": not doc,
                    "denied": bool(doc) and not can_access_document(doc, user, guest_id),
                    "not_ready": bool(doc) and doc.status != "ready",
                },
            ).action,
            {
                "missing": lambda: _http("missing", "Document not found"),
                "conflict": lambda: _http("conflict", "Document not ready"),
                "ok": lambda: None,
            },
        )

        scope_preview = pick(bool(body.scope), lambda: body.scope.model_dump(), lambda: {})

        def _check_rag() -> None:
            from app.services.rag_window import is_rag_window_ready

            pick(
                not is_rag_window_ready(db, doc.id, doc),
                lambda: _http("conflict", "Preparing chat context…"),
                lambda: None,
            )

        pick(scope_preview.get("current_page") is not None, _check_rag, lambda: None)

        include_image = body.use_vision or is_image_document(doc)

        def _resolve_model() -> None:
            from app.services.llm_registry import resolve_chat_model

            try:
                resolve_chat_model(db, model_id=body.model_id, require_vision=include_image)
            except HTTPException:
                raise
            except Exception as exc:
                _raise_from(
                    exc,
                    HTTPException(status_code=503, detail="No chat model available"),
                )

        pick(body.model_id is not None, _resolve_model, lambda: None)

        reserve_message_slot(db, user=user, request=request, demo_cookie=guest_id)

        surface = _chat_surface(scope_preview.get("mode"))
        thread = _get_or_create_thread(db, _account_id(user), doc, surface, guest_id=guest_id)
        history = (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(plan_chat_thread_history())
            .all()
        )
        history = list(reversed(history))
        prior = list(
            map(
                lambda m: {"role": m.role, "content": m.content},
                filter(
                    lambda m: (m.content or "").strip()
                    and not (
                        m.role == "assistant" and _is_transient_assistant_error(m.content)
                    ),
                    history,
                ),
            )
        )
        prior = _shrink_prior_messages(prior)

        db.add(ChatMessage(thread_id=thread.id, role="user", content=body.message))
        db.commit()

        return {
            "document_id": doc.id,
            "thread_id": thread.id,
            "prior_messages": prior,
            "request_message": body.message,
            "request_model_id": body.model_id,
            "request_scope": pick(bool(body.scope), lambda: body.scope.model_dump(), lambda: {}),
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
        stream_db = SessionLocal()
        try:
            yield {"event": "status", "data": json.dumps({"phase": "thinking"})}
            try:
                doc = stream_db.get(Document, document_id)
                accessible = bool(doc) and can_access_document(doc, user, guest_id)
                apply(
                    evaluate_presence(pick(accessible, lambda: doc, lambda: None)).action,
                    {
                        "missing": lambda: _raise(
                            _StreamHalt(_stream_error_event("Document not found."))
                        ),
                        "empty": lambda: _raise(
                            _StreamHalt(_stream_error_event("Document not found."))
                        ),
                        "ok": lambda: None,
                    },
                )
                scope = pick(bool(request_scope), lambda: dict(request_scope), lambda: {})
                mode = str(request_scope.get("mode") or "").lower()
                read_mode = mode == "read"
                brainstorm_mode = mode == "brainstorm"
                socratic_mode = mode == "socratic"
                scope["mode"] = pick(
                    read_mode, lambda: "read", lambda: request_scope.get("mode") or None
                )
                pick(
                    should_pin_learn_queue(scope_mode=mode),
                    lambda: scope.update(
                        learn_scope_fields(
                            stream_db,
                            doc.id,
                            doc,
                            request_scope=request_scope,
                            user=user,
                            guest_id=guest_id,
                        )
                    ),
                    lambda: None,
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
                image = is_image_document(doc)
                for ev in pick(
                    image,
                    lambda: [],
                    lambda: [{"event": "status", "data": json.dumps({"phase": "retrieving"})}],
                ):
                    yield ev

                async def _from_image() -> tuple[dict, str | None]:
                    return (
                        {
                            "citations": [],
                            "messages": prior_messages,
                            "context_block": "",
                            "context_note": "",
                        },
                        build_learn_chat_context(
                            stream_db,
                            doc.id,
                            doc,
                            scope=scope,
                            user=user,
                            guest_id=guest_id,
                        ),
                    )

                async def _from_text() -> tuple[dict, str | None]:
                    def _retrieve_for_chat() -> dict:
                        with SessionLocal() as retrieve_db:
                            retrieve_doc = retrieve_db.get(Document, document_id)
                            apply(
                                evaluate_presence(retrieve_doc).action,
                                {
                                    "missing": lambda: _http("missing", "Document not found"),
                                    "empty": lambda: _http("missing", "Document not found"),
                                    "ok": lambda: None,
                                },
                            )
                            return run_retrieve(
                                retrieve_db,
                                document_ids=doc_ids,
                                query=_retrieval_query(request_message),
                                scope=scope,
                                mentions=scope.get("mentions") or [],
                                prior_messages=prior_messages,
                            )

                    def _learn_context_for_chat() -> str | None:
                        def _load() -> str | None:
                            with SessionLocal() as learn_db:
                                learn_doc = learn_db.get(Document, document_id)
                                return pick(
                                    not learn_doc,
                                    lambda: None,
                                    lambda: build_learn_chat_context(
                                        learn_db,
                                        learn_doc.id,
                                        learn_doc,
                                        scope=scope,
                                        user=user,
                                        guest_id=guest_id,
                                    ),
                                )

                        return pick(
                            brainstorm_mode or socratic_mode,
                            lambda: None,
                            _load,
                        )

                    return await asyncio.gather(
                        asyncio.to_thread(_retrieve_for_chat),
                        asyncio.to_thread(_learn_context_for_chat),
                    )

                retrieved, learn_context = await pick(image, _from_image, _from_text)
                citations = retrieved.get("citations", [])
                system = get_prompt(
                    stream_db,
                    plan_chat_system_prompt_key(
                        socratic=socratic_mode, brainstorm=brainstorm_mode
                    ),
                )
                messages = [{"role": "system", "content": system}]
                model_history = pick(
                    prefetched_ref,
                    lambda: [],
                    lambda: retrieved.get("messages") or prior_messages,
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
                trailer = plan_chat_trailers(
                    prefetched_reference=prefetched_ref,
                    learn_context=learn_context,
                    message=request_message,
                    has_history=bool(prior_messages),
                )
                trailer_parts: list[str] = []
                pick(
                    trailer.prefetched_directive,
                    lambda: trailer_parts.append(_PREFETCHED_REFERENCE_DIRECTIVE),
                    lambda: None,
                )
                pick(
                    trailer.mcq_guardrail,
                    lambda: trailer_parts.append(_MCQ_ANSWER_GUARDRAIL),
                    lambda: None,
                )
                pick(
                    trailer.quiz_format,
                    lambda: trailer_parts.append(get_prompt(stream_db, "mcq_format")),
                    lambda: None,
                )
                user_content = pick(
                    bool(trailer_parts),
                    lambda: user_content + "\n\n" + "\n\n".join(trailer_parts),
                    lambda: user_content,
                )
                body_parts: list[str] = []
                learn_lead = learn_context or ""
                pick(bool(learn_lead), lambda: body_parts.append(learn_lead), lambda: None)
                pick(
                    bool(context_block),
                    lambda: body_parts.append(
                        "Document excerpts (retrieved for this turn):\n\n" + context_block
                    ),
                    lambda: None,
                )
                body_parts.append(user_content)
                final_user_content = pick(
                    len(body_parts) == 1,
                    lambda: body_parts[0],
                    lambda: "\n\n---\n\n".join(body_parts),
                )
                messages.append({"role": "user", "content": final_user_content})

                has_history = bool(prior_messages)
                cache_eligible = (not brainstorm_mode) and _cache_eligible(
                    include_image=include_image,
                    selection_text=scope.get("selection_text"),
                    has_citations=bool(citations),
                    doc_count=len(doc_ids),
                    has_history=has_history,
                    conversational_followup=is_conversational_followup(request_message),
                    prefetched_reference=prefetched_ref,
                )
                artifact_id, artifact_captured_at = _artifact_ref(doc)
                cache_scope = dict(scope)
                pick(
                    has_history,
                    lambda: cache_scope.__setitem__(
                        "history_digest", _history_digest(prior_messages)
                    ),
                    lambda: None,
                )

                async def _lookup_cache() -> tuple[list[float] | None, dict | None]:
                    query_embedding = await asyncio.to_thread(embed_query, request_message)
                    cached = await asyncio.to_thread(
                        get_cached_response,
                        stream_db,
                        document_id=artifact_id,
                        artifact_captured_at=artifact_captured_at,
                        scope=cache_scope,
                        query_embedding=query_embedding,
                    )
                    return query_embedding, cached

                async def _skip_cache() -> tuple[list[float] | None, dict | None]:
                    pick(
                        has_history and is_conversational_followup(request_message),
                        lambda: logger.debug(
                            "chat cache skipped: conversational follow-up doc=%s",
                            document_id,
                        ),
                        lambda: None,
                    )
                    return None, None

                query_embedding, cached = await pick(
                    cache_eligible, _lookup_cache, _skip_cache
                )
            except _StreamHalt as halt:
                yield halt.event
                return
            except Exception as exc:
                logger.exception("chat setup failed: %s", exc)
                yield _stream_error_event(_user_facing_chat_error(exc))
                return

            async def _replay_cached():
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

            async def _stream_live():
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
                    pick(
                        not full.strip(),
                        lambda: _raise(RuntimeError("empty model response")),
                        lambda: None,
                    )
                    stream_db.add(
                        ChatMessage(
                            thread_id=thread_id,
                            role="assistant",
                            content=full,
                            citations={"sources": citations},
                        )
                    )
                    stream_db.commit()

                    async def _store() -> None:
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

                    async def _noop() -> None:
                        return None

                    await pick(cache_eligible, _store, _noop)
                    yield {"event": "sources", "data": json.dumps({"citations": citations})}
                    yield {"event": "done", "data": "{}"}
                except Exception as exc:
                    logger.exception("chat stream failed: %s", exc)
                    yield _stream_error_event(_user_facing_chat_error(exc))

            agen = pick(bool(cached), _replay_cached, _stream_live)
            async for ev in agen:
                yield ev
        finally:
            stream_db.close()

    return EventSourceResponse(
        event_generator(),
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
        },
    )


class _StreamHalt(Exception):
    def __init__(self, event: dict[str, str]) -> None:
        self.event = event


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
    pick(
        body.correct_index >= len(body.options) or body.selected_index >= len(body.options),
        lambda: _raise(HTTPException(status_code=400, detail="Invalid option index")),
        lambda: None,
    )

    need_ctx = not (body.explanation or "").strip() and not is_image_document(doc)

    async def _fetch() -> str:
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
        return "\n\n".join(c["text"] for c in chunks[:top_n])

    async def _empty() -> str:
        return ""

    context = await pick(need_ctx, _fetch, _empty)

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
