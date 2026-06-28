"""Explain mode — topic outline + plain-language explanations (additive to MCQ)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account
from app.services.auth import get_optional_user
from app.services.guest_session import guest_session_for_read
from app.api.access import require_document
from app.services.topics import (
    ensure_explanation,
    ensure_topics,
    find_topic,
    load_outline,
)

router = APIRouter()


@router.get("/{artifact_id}/topics")
def get_topics(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """The document's topic outline. Generates in the background on first request.

    Returns {status: indexing|generating|ready|failed, topics: [{key,title,summary}]}.
    """
    doc = require_document(db, artifact_id, user, guest_id)
    if doc.status != "ready":
        return {"status": "indexing", "topics": []}
    return ensure_topics(db, artifact_id)


@router.get("/{artifact_id}/topics/{topic_key}/explain")
def explain_topic_endpoint(
    artifact_id: uuid.UUID,
    topic_key: str,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """High-level, plain-language explanation of one topic.

    Generation runs in a background worker (off the answer path), so this returns
    immediately with status 'generating' the first time and the client polls — no
    synchronous LLM call that could exceed the ingress timeout. Cached after first build.
    """
    require_document(db, artifact_id, user, guest_id)

    outline = load_outline(db, artifact_id)
    if outline["status"] != "ready":
        # Outline still building — the client should poll /topics first.
        return {"status": outline["status"], "topic_key": topic_key, "explanation": None}
    topic = find_topic(outline["topics"], topic_key)
    if not topic:
        raise HTTPException(status_code=404, detail="Unknown topic")

    state = ensure_explanation(
        db, artifact_id, topic_key, title=topic["title"], summary=topic.get("summary", "")
    )
    return {"status": state["status"], "topic_key": topic_key, "explanation": state["explanation"]}
