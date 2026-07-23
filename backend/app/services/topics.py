"""Explain feature — topic outline storage + orchestration.

Additive to MCQ. Reads the document's RAG chunks to build an ordered topic outline
(generated off the answer path by a worker), then serves it to the UI. Per-topic
plain-language explanations are generated + cached on demand (explain_topic).

Persistence + worker skeleton are shared via ``app.services.artifact_store``; this
module owns the two distinct artifact shapes (one-per-document outline + per-topic
explanation keyed by ``topic_key``) and their read-path invalidation rules.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.services.artifact_store import ArtifactStore, coerce_jsonb, run_artifact_generation
from app.services.source_fingerprint import is_artifact_stale, mark_artifact_fresh

_OUTLINE_STORE = ArtifactStore(
    table="qb.document_topics",
    key_cols=[],
    payload_col="outline",
)
_EXPLANATION_STORE = ArtifactStore(
    table="qb.topic_explanation",
    key_cols=["topic_key"],
    payload_col="explanation",
    cast_jsonb=False,  # explanation is TEXT, not JSONB
)


def load_outline(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, topics, error}. status: missing|generating|ready|failed."""
    row = _OUTLINE_STORE.load_row(db, document_id)
    if not row:
        return {"status": "missing", "topics": [], "error": None}
    return {"status": row["status"], "topics": coerce_jsonb(row["outline"]) or [], "error": row["error"]}


def ensure_topics(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Read path for GET /topics: return the outline, enqueuing generation if needed.

    O(1): one indexed read; generation runs in a background worker (never inline), so
    the request returns immediately with status 'generating' the first time.
    """
    state = load_outline(db, document_id)
    if state["status"] == "ready" and not is_artifact_stale(db, document_id, "topics"):
        return state
    if state["status"] == "generating":
        return state
    # missing, failed, or stale → kick a background generation
    from app.services.jobs import enqueue_topics

    _OUTLINE_STORE.set_status(db, document_id, "generating")
    db.commit()
    enqueue_topics(db, document_id)
    return {"status": "generating", "topics": [], "error": None}


def find_topic(topics: list[dict[str, str]], topic_key: str) -> dict[str, str] | None:
    return next((t for t in topics if t.get("key") == topic_key), None)


def save_outline(db: Session, document_id: uuid.UUID, topics: list[dict[str, str]]) -> None:
    """Persist a finished topic outline (status='ready'). Used by the worker + tests."""
    _OUTLINE_STORE.save(db, document_id, topics)


def load_explanation_row(
    db: Session, document_id: uuid.UUID, topic_key: str
) -> dict[str, Any] | None:
    """Return {status, explanation, error} for one topic, or None if never requested."""
    row = _EXPLANATION_STORE.load_row(db, document_id, topic_key=topic_key)
    if not row:
        return None
    return {"status": row["status"], "explanation": row["explanation"], "error": row["error"]}


def ensure_explanation(
    db: Session, document_id: uuid.UUID, topic_key: str, *, title: str, summary: str
) -> dict[str, Any]:
    """Read path for one explanation: return it, enqueuing generation if needed.

    Off the answer path — explanation is built by a worker (never inline), so the request
    returns immediately with status 'generating' the first time, avoiding ingress timeouts
    on slow LLM calls.
    """
    fp_key = f"explain:{topic_key}"
    row = load_explanation_row(db, document_id, topic_key)
    if (
        row
        and row["status"] == "ready"
        and row["explanation"]
        and not is_artifact_stale(db, document_id, fp_key)
    ):
        return row
    if row and row["status"] == "generating":
        return {"status": "generating", "explanation": None, "error": None}
    from app.services.jobs import enqueue_explanation

    _EXPLANATION_STORE.set_status(db, document_id, "generating", topic_key=topic_key)
    db.commit()
    enqueue_explanation(db, document_id, topic_key, title=title, summary=summary)
    return {"status": "generating", "explanation": None, "error": None}


def run_explanation(
    db: Session, document_id: uuid.UUID, topic_key: str, *, title: str, summary: str
) -> str:
    """Worker entry: generate and cache one plain-language topic explanation."""
    from app.graphs.topics_graph import explain_topic

    result = run_artifact_generation(
        db,
        document_id,
        store=_EXPLANATION_STORE,
        generate=lambda: explain_topic(db, document_id, title=title, summary=summary),
        is_complete=lambda explanation: bool(explanation.strip()),
        empty_error="empty_explanation",
        key_and_extra={"topic_key": topic_key},
    )
    if isinstance(result, str) and result.strip():
        mark_artifact_fresh(db, document_id, f"explain:{topic_key}")
        db.commit()
    return result


def run_topics_generation(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Worker entry: build and persist the topic outline for a document."""
    from app.graphs.topics_graph import generate_topic_outline

    result = run_artifact_generation(
        db,
        document_id,
        store=_OUTLINE_STORE,
        generate=lambda: generate_topic_outline(db, document_id),
        is_complete=bool,
        empty_error="no_topics_found",
    )
    if result:
        mark_artifact_fresh(db, document_id, "topics")
        db.commit()
    return result

