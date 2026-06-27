"""Memory Palace storage + orchestration (Magnetic Memory Method).

Additive to MCQ/Explain/Notes. One palace per document, generated off the answer path by
a worker and cached. The read path (ensure_palace) returns immediately, enqueuing
generation the first time — and regenerating when the learner picks a different place
(``setting``) so they can anchor the journey somewhere they personally know well.
Raw parameterized SQL on qb.document_memory_palace.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def load_palace(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, setting, palace, error}. status: missing|generating|ready|failed."""
    row = db.execute(
        text(
            "SELECT setting, palace, status, error FROM qb.document_memory_palace "
            "WHERE document_id = :id"
        ),
        {"id": document_id},
    ).mappings().first()
    if not row:
        return {"status": "missing", "setting": "", "palace": None, "error": None}
    palace = row["palace"]
    if isinstance(palace, str):
        palace = json.loads(palace)
    return {
        "status": row["status"],
        "setting": row["setting"] or "",
        "palace": palace or None,
        "error": row["error"],
    }


def _set_status(
    db: Session, document_id: uuid.UUID, status: str, *, setting: str, error: str | None = None
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_memory_palace (document_id, setting, status, error, updated_at)
            VALUES (:id, :setting, :status, :error, now())
            ON CONFLICT (document_id)
            DO UPDATE SET setting = EXCLUDED.setting, status = EXCLUDED.status,
                          error = EXCLUDED.error, updated_at = now()
            """
        ),
        {"id": document_id, "setting": setting, "status": status, "error": error},
    )


def save_palace(db: Session, document_id: uuid.UUID, setting: str, palace: dict) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_memory_palace (document_id, setting, palace, status, error, updated_at)
            VALUES (:id, :setting, CAST(:palace AS jsonb), 'ready', NULL, now())
            ON CONFLICT (document_id)
            DO UPDATE SET setting = EXCLUDED.setting, palace = EXCLUDED.palace,
                          status = 'ready', error = NULL, updated_at = now()
            """
        ),
        {"id": document_id, "setting": setting or palace.get("setting", ""), "palace": json.dumps(palace)},
    )


def ensure_palace(
    db: Session, document_id: uuid.UUID, *, setting: str | None = None
) -> dict[str, Any]:
    """Read path: return the palace, (re)generating in the background when needed.

    No setting → build once (AI picks a familiar place). An explicit setting that differs
    from the stored one → regenerate the journey in that place.
    """
    state = load_palace(db, document_id)
    requested = (setting or "").strip()

    if requested:
        if state["status"] == "ready" and (state["setting"] or "").strip().lower() == requested.lower():
            return state
        if state["status"] == "generating" and (state["setting"] or "").strip().lower() == requested.lower():
            return state
        return _kick(db, document_id, requested)

    if state["status"] in ("ready", "generating"):
        return state
    return _kick(db, document_id, "")


def _kick(db: Session, document_id: uuid.UUID, setting: str) -> dict[str, Any]:
    from app.services.jobs import enqueue_memory_palace

    _set_status(db, document_id, "generating", setting=setting)
    db.commit()
    enqueue_memory_palace(db, document_id, setting)
    return {"status": "generating", "setting": setting, "palace": None, "error": None}


def run_palace_generation(db: Session, document_id: uuid.UUID, setting: str = "") -> dict:
    """Worker entry: build and persist the memory palace for a document."""
    import asyncio

    from app.graphs.memory_palace_graph import generate_memory_palace

    _set_status(db, document_id, "generating", setting=setting)
    db.commit()
    try:
        palace = asyncio.run(generate_memory_palace(db, document_id, setting=setting))
    except Exception as exc:
        _set_status(db, document_id, "failed", setting=setting, error=str(exc)[:500])
        db.commit()
        raise
    if palace and palace.get("stations"):
        save_palace(db, document_id, setting, palace)
    else:
        _set_status(db, document_id, "failed", setting=setting, error="no_palace_generated")
    db.commit()
    return palace
