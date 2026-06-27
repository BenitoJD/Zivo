"""Explain mode — topic outline + plain-language explanations (additive to MCQ)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, Document
from app.services.auth import get_optional_user
from app.services.guest import can_access_document
from app.services.guest_session import guest_session_for_read
from app.services.topics import (
    ensure_topics,
    find_topic,
    load_explanation,
    load_outline,
    save_explanation,
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
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Not found")
    if doc.status != "ready":
        return {"status": "indexing", "topics": []}
    return ensure_topics(db, artifact_id)


@router.get("/{artifact_id}/topics/{topic_key}/explain")
async def explain_topic_endpoint(
    artifact_id: uuid.UUID,
    topic_key: str,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """High-level, plain-language explanation of one topic. Cached after first build."""
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Not found")

    cached = load_explanation(db, artifact_id, topic_key)
    if cached:
        return {"status": "ready", "topic_key": topic_key, "explanation": cached}

    outline = load_outline(db, artifact_id)
    if outline["status"] != "ready":
        # Outline still building — the client should poll /topics first.
        return {"status": outline["status"], "topic_key": topic_key, "explanation": None}
    topic = find_topic(outline["topics"], topic_key)
    if not topic:
        raise HTTPException(status_code=404, detail="Unknown topic")

    from app.graphs.topics_graph import explain_topic

    explanation = await explain_topic(
        db, artifact_id, title=topic["title"], summary=topic.get("summary", "")
    )
    if not explanation:
        return {"status": "failed", "topic_key": topic_key, "explanation": None}
    save_explanation(db, artifact_id, topic_key, explanation)
    db.commit()
    return {"status": "ready", "topic_key": topic_key, "explanation": explanation}
