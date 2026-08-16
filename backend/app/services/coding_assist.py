"""Assertion-scoped coding assistant — no PDF / RAG required.

Public curated problems often have no document. Threads key off assertion_id
(+ recorded_at) with surface ``coding`` (guest-scoped as ``g:{guest}:coding``).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db import SessionLocal
from app.engine_runtime import choose, pick
from app.models import Account, ChatMessage, ChatThread
from app.services.code_execution import LANGUAGES
from app.services.llm_router import stream_chat_completion
from app.services.open_response import plan_coding_assist_sample_display
from app.services.tutor_retrieval import (
    plan_coding_assist_code_chars,
    plan_coding_assist_history,
)
from app.services.usage import reserve_message_slot

logger = logging.getLogger(__name__)

_SURFACE = "coding"
_LIST_MESSAGES_LIMIT = 200

_ASSIST_SYSTEM = """You are Zivo's coding practice assistant for one programming problem.
Help the learner understand the statement, debug compile/runtime errors, and reason about
algorithms. Prefer Socratic hints over dumping a full solution.
Rules:
- Ground answers in the problem statement and sample cases provided in context.
- Do not invent hidden tests or claim you ran code unless the context includes a run status.
- Do not paste a complete reference solution unless the learner explicitly asks for the full
  solution, or they have clearly failed repeatedly and ask for a worked answer.
- Keep answers concise. Use short code snippets only when they teach a point.
- If their language or code is in context, tailor hints to that attempt.
"""


def _thread_surface(*, account_id: uuid.UUID | None, guest_id: str | None) -> str:
    return choose(account_id is None and bool(guest_id), f"g:{guest_id}:{_SURFACE}", _SURFACE)


def get_or_create_coding_thread(
    db: Session,
    *,
    account_id: uuid.UUID | None,
    assertion_id: uuid.UUID,
    recorded_at: datetime,
    guest_id: str | None = None,
) -> ChatThread:
    thread_surface = _thread_surface(account_id=account_id, guest_id=guest_id)
    thread = (
        db.query(ChatThread)
        .filter(
            ChatThread.artifact_id == assertion_id,
            ChatThread.artifact_captured_at == recorded_at,
            ChatThread.account_id == account_id,
            ChatThread.surface == thread_surface,
        )
        .order_by(ChatThread.version.desc())
        .first()
    )
    return pick(bool(thread), lambda: thread, lambda: _create_thread(
        db,
        account_id=account_id,
        assertion_id=assertion_id,
        recorded_at=recorded_at,
        thread_surface=thread_surface,
    ))


def _create_thread(
    db: Session,
    *,
    account_id: uuid.UUID | None,
    assertion_id: uuid.UUID,
    recorded_at: datetime,
    thread_surface: str,
) -> ChatThread:
    thread = ChatThread(
        account_id=account_id,
        artifact_id=assertion_id,
        artifact_captured_at=recorded_at,
        surface=thread_surface,
        version=1,
    )
    try:
        db.add(thread)
        db.commit()
        db.refresh(thread)
        return thread
    except IntegrityError as exc:
        db.rollback()
        existing = (
            db.query(ChatThread)
            .filter(
                ChatThread.artifact_id == assertion_id,
                ChatThread.artifact_captured_at == recorded_at,
                ChatThread.account_id == account_id,
                ChatThread.surface == thread_surface,
                ChatThread.version == 1,
            )
            .first()
        )
        return _existing_thread_or_reraise(existing, exc)


def _existing_thread_or_reraise(existing: ChatThread | None, exc: IntegrityError) -> ChatThread:
    return pick(bool(existing), lambda: existing, lambda: _raise_integrity(exc))


def _raise_integrity(exc: IntegrityError) -> ChatThread:
    raise exc


def list_assist_messages(
    db: Session,
    *,
    account_id: uuid.UUID | None,
    assertion_id: uuid.UUID,
    recorded_at: datetime,
    guest_id: str | None,
    offset: int = 0,
) -> list[ChatMessage]:
    pick(
        offset < 0,
        lambda: _raise_offset(),
        lambda: None,
    )
    thread = get_or_create_coding_thread(
        db,
        account_id=account_id,
        assertion_id=assertion_id,
        recorded_at=recorded_at,
        guest_id=guest_id,
    )
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.asc())
        .offset(offset)
        .limit(_LIST_MESSAGES_LIMIT)
        .all()
    )


def _raise_offset() -> None:
    raise HTTPException(status_code=400, detail="offset must be >= 0")


def clear_assist_thread(
    db: Session,
    *,
    account_id: uuid.UUID | None,
    assertion_id: uuid.UUID,
    recorded_at: datetime,
    guest_id: str | None,
) -> int:
    current = get_or_create_coding_thread(
        db,
        account_id=account_id,
        assertion_id=assertion_id,
        recorded_at=recorded_at,
        guest_id=guest_id,
    )
    new_version = current.version + 1
    thread = ChatThread(
        account_id=account_id,
        artifact_id=assertion_id,
        artifact_captured_at=recorded_at,
        surface=current.surface,
        version=new_version,
    )
    db.add(thread)
    db.commit()
    return new_version


def _problem_context_block(public: dict[str, Any]) -> str:
    samples = public.get("sample_tests") or []
    sample_lines: list[str] = []
    for i, t in enumerate(samples[: plan_coding_assist_sample_display()], start=1):
        sample_lines.append(
            f"Sample {i}:\nstdin:\n{t.get('stdin', '')}\nexpected:\n{t.get('expected_output', '')}"
        )
    tags = public.get("tags") or []
    return "\n".join(
        [
            f"Title: {public.get('title') or 'Untitled'}",
            f"Difficulty: {public.get('difficulty') or 'medium'}",
            f"Tags: {choose(bool(tags), ', '.join(str(t) for t in tags), '(none)')}",
            "",
            "Statement:",
            str(public.get("statement") or "").strip() or "(empty)",
            "",
            *sample_lines,
        ]
    )


def _editor_context_block(
    *,
    code: str | None,
    language_id: int | None,
    stdin: str | None,
    last_status: str | None,
) -> str:
    parts: list[str] = []
    pick(
        language_id is not None,
        lambda: parts.append(
            f"Language: {LANGUAGES.get(int(language_id), f'language_id={language_id}')} (id {language_id})"
        ),
        lambda: None,
    )
    pick(bool(last_status), lambda: parts.append(f"Last run/submit status: {last_status}"), lambda: None)
    pick(
        stdin is not None and bool(str(stdin).strip()),
        lambda: parts.append(f"Custom stdin:\n{stdin}"),
        lambda: None,
    )
    pick(
        code is not None and bool(str(code).strip()),
        lambda: parts.append(
            f"Learner's current code:\n```\n{str(code)[: plan_coding_assist_code_chars()]}\n```"
        ),
        lambda: None,
    )
    return "\n\n".join(parts)


def prepare_assist_stream(
    *,
    request: Request,
    user: Account | None,
    guest_id: str | None,
    assertion_id: uuid.UUID,
    recorded_at: datetime,
    public_problem: dict[str, Any],
    message: str,
    code: str | None,
    language_id: int | None,
    stdin: str | None,
    last_status: str | None,
) -> dict[str, Any]:
    """Sync setup: reserve slot, persist user turn, return stream args."""
    with SessionLocal() as db:
        reserve_message_slot(db, user=user, request=request, demo_cookie=guest_id)
        account_id = pick(bool(user), lambda: user.id, lambda: None)
        thread = get_or_create_coding_thread(
            db,
            account_id=account_id,
            assertion_id=assertion_id,
            recorded_at=recorded_at,
            guest_id=guest_id,
        )
        history = (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(plan_coding_assist_history())
            .all()
        )
        history = list(reversed(history))
        prior = [
            {"role": m.role, "content": m.content}
            for m in filter(lambda m: bool((m.content or "").strip()), history)
        ]
        db.add(ChatMessage(thread_id=thread.id, role="user", content=message))
        db.commit()
        return {
            "thread_id": thread.id,
            "prior_messages": prior,
            "request_message": message,
            "problem_block": _problem_context_block(public_problem),
            "editor_block": _editor_context_block(
                code=code,
                language_id=language_id,
                stdin=stdin,
                last_status=last_status,
            ),
        }


def _stream_error_event(message: str) -> dict[str, str]:
    return {"event": "error", "data": json.dumps({"message": message})}


async def assist_event_stream(setup: dict[str, Any]) -> EventSourceResponse:
    thread_id = setup["thread_id"]
    prior_messages: list[dict[str, str]] = setup["prior_messages"]
    request_message: str = setup["request_message"]
    problem_block: str = setup["problem_block"]
    editor_block: str = setup["editor_block"]

    async def event_generator() -> Any:
        stream_db = SessionLocal()
        try:
            yield {"event": "status", "data": json.dumps({"phase": "thinking"})}
            try:
                messages: list[dict[str, str]] = [{"role": "system", "content": _ASSIST_SYSTEM}]
                messages.extend(prior_messages)
                user_parts = ["Problem context:\n" + problem_block]
                pick(
                    bool(editor_block.strip()),
                    lambda: user_parts.append("Editor context:\n" + editor_block),
                    lambda: None,
                )
                user_parts.append("Learner question:\n" + request_message)
                messages.append({"role": "user", "content": "\n\n".join(user_parts)})

                full = ""
                async for token in stream_chat_completion(
                    messages, stream_db, model_id=None, log_tag="coding_assist"
                ):
                    full += token
                    yield {"event": "token", "data": json.dumps({"text": token})}
                pick(not full.strip(), lambda: _raise_empty(), lambda: None)
                stream_db.add(
                    ChatMessage(thread_id=thread_id, role="assistant", content=full)
                )
                stream_db.commit()
                yield {"event": "done", "data": "{}"}
            except Exception as exc:
                logger.exception("coding assist stream failed: %s", exc)
                yield _stream_error_event(
                    "Assistant is busy right now. Try again in a moment."
                )
        finally:
            stream_db.close()

    return EventSourceResponse(event_generator())


def _raise_empty() -> None:
    raise RuntimeError("empty model response")
