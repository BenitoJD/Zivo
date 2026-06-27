"""Explain feature — topic outline storage + orchestration.

Additive to MCQ. Reads the document's RAG chunks to build an ordered topic outline
(generated off the answer path by a worker), then serves it to the UI. Per-topic
plain-language explanations are generated + cached on demand (explain_topic).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def load_outline(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, topics, error}. status: missing|generating|ready|failed."""
    row = db.execute(
        text("SELECT outline, status, error FROM qb.document_topics WHERE document_id = :id"),
        {"id": document_id},
    ).mappings().first()
    if not row:
        return {"status": "missing", "topics": [], "error": None}
    outline = row["outline"]
    if isinstance(outline, str):
        outline = json.loads(outline)
    return {"status": row["status"], "topics": outline or [], "error": row["error"]}


def _set_status(db: Session, document_id: uuid.UUID, status: str, *, error: str | None = None) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_topics (document_id, status, error, updated_at)
            VALUES (:id, :status, :error, now())
            ON CONFLICT (document_id)
            DO UPDATE SET status = EXCLUDED.status, error = EXCLUDED.error, updated_at = now()
            """
        ),
        {"id": document_id, "status": status, "error": error},
    )


def save_outline(db: Session, document_id: uuid.UUID, topics: list[dict[str, str]]) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_topics (document_id, outline, status, error, updated_at)
            VALUES (:id, CAST(:outline AS jsonb), 'ready', NULL, now())
            ON CONFLICT (document_id)
            DO UPDATE SET outline = EXCLUDED.outline, status = 'ready', error = NULL, updated_at = now()
            """
        ),
        {"id": document_id, "outline": json.dumps(topics)},
    )


def ensure_topics(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Read path for GET /topics: return the outline, enqueuing generation if needed.

    O(1): one indexed read; generation runs in a background worker (never inline), so
    the request returns immediately with status 'generating' the first time.
    """
    state = load_outline(db, document_id)
    if state["status"] in ("ready", "generating"):
        return state
    # missing or failed → kick a background generation
    from app.services.jobs import enqueue_topics

    _set_status(db, document_id, "generating")
    db.commit()
    enqueue_topics(db, document_id)
    return {"status": "generating", "topics": [], "error": None}


def find_topic(topics: list[dict[str, str]], topic_key: str) -> dict[str, str] | None:
    return next((t for t in topics if t.get("key") == topic_key), None)


def load_explanation(db: Session, document_id: uuid.UUID, topic_key: str) -> str | None:
    return db.execute(
        text(
            "SELECT explanation FROM qb.topic_explanation "
            "WHERE document_id = :d AND topic_key = :k"
        ),
        {"d": document_id, "k": topic_key},
    ).scalar()


def save_explanation(db: Session, document_id: uuid.UUID, topic_key: str, explanation: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.topic_explanation (document_id, topic_key, explanation, updated_at)
            VALUES (:d, :k, :e, now())
            ON CONFLICT (document_id, topic_key)
            DO UPDATE SET explanation = EXCLUDED.explanation, updated_at = now()
            """
        ),
        {"d": document_id, "k": topic_key, "e": explanation},
    )


def run_topics_generation(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Worker entry: build and persist the topic outline for a document."""
    import asyncio

    from app.graphs.topics_graph import generate_topic_outline

    _set_status(db, document_id, "generating")
    db.commit()
    try:
        topics = asyncio.run(generate_topic_outline(db, document_id))
    except Exception as exc:
        _set_status(db, document_id, "failed", error=str(exc)[:500])
        db.commit()
        raise
    if topics:
        save_outline(db, document_id, topics)
    else:
        _set_status(db, document_id, "failed", error="no_topics_found")
    db.commit()
    return topics
