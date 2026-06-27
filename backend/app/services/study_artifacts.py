"""Study-artifact storage + orchestration: notes, cheat sheets, flashcards.

Additive to MCQ/Explain. Each artifact is generated off the answer path by a worker and
cached per document; the read path (ensure_*) returns immediately, enqueuing generation
the first time. Raw parameterized SQL on qb.document_notes / qb.document_flashcards.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

NOTE_KINDS = ("notes", "cheatsheet")


# --------------------------------------------------------------------------- notes
def load_notes(db: Session, document_id: uuid.UUID, kind: str) -> dict[str, Any]:
    """Return {status, content, error}. status: missing|generating|ready|failed."""
    row = db.execute(
        text(
            "SELECT content, status, error FROM qb.document_notes "
            "WHERE document_id = :id AND kind = :kind"
        ),
        {"id": document_id, "kind": kind},
    ).mappings().first()
    if not row:
        return {"status": "missing", "content": "", "error": None}
    return {"status": row["status"], "content": row["content"] or "", "error": row["error"]}


def _set_notes_status(
    db: Session, document_id: uuid.UUID, kind: str, status: str, *, error: str | None = None
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_notes (document_id, kind, status, error, updated_at)
            VALUES (:id, :kind, :status, :error, now())
            ON CONFLICT (document_id, kind)
            DO UPDATE SET status = EXCLUDED.status, error = EXCLUDED.error, updated_at = now()
            """
        ),
        {"id": document_id, "kind": kind, "status": status, "error": error},
    )


def save_notes(db: Session, document_id: uuid.UUID, kind: str, content: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_notes (document_id, kind, content, status, error, updated_at)
            VALUES (:id, :kind, :content, 'ready', NULL, now())
            ON CONFLICT (document_id, kind)
            DO UPDATE SET content = EXCLUDED.content, status = 'ready', error = NULL, updated_at = now()
            """
        ),
        {"id": document_id, "kind": kind, "content": content},
    )


def ensure_notes(db: Session, document_id: uuid.UUID, kind: str) -> dict[str, Any]:
    if kind not in NOTE_KINDS:
        kind = "notes"
    state = load_notes(db, document_id, kind)
    if state["status"] in ("ready", "generating"):
        return state
    from app.services.jobs import enqueue_notes

    _set_notes_status(db, document_id, kind, "generating")
    db.commit()
    enqueue_notes(db, document_id, kind)
    return {"status": "generating", "content": "", "error": None}


def run_notes_generation(db: Session, document_id: uuid.UUID, kind: str) -> str:
    """Worker entry: build and persist study notes (or a cheat sheet) for a document."""
    import asyncio

    from app.graphs.notes_graph import generate_notes

    if kind not in NOTE_KINDS:
        kind = "notes"
    _set_notes_status(db, document_id, kind, "generating")
    db.commit()
    try:
        content = asyncio.run(generate_notes(db, document_id, kind=kind))
    except Exception as exc:
        _set_notes_status(db, document_id, kind, "failed", error=str(exc)[:500])
        db.commit()
        raise
    if content.strip():
        save_notes(db, document_id, kind, content)
    else:
        _set_notes_status(db, document_id, kind, "failed", error="no_notes_generated")
    db.commit()
    return content


# ----------------------------------------------------------------------- flashcards
def load_flashcards(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, cards, error}. status: missing|generating|ready|failed."""
    row = db.execute(
        text("SELECT cards, status, error FROM qb.document_flashcards WHERE document_id = :id"),
        {"id": document_id},
    ).mappings().first()
    if not row:
        return {"status": "missing", "cards": [], "error": None}
    cards = row["cards"]
    if isinstance(cards, str):
        cards = json.loads(cards)
    return {"status": row["status"], "cards": cards or [], "error": row["error"]}


def _set_flashcards_status(
    db: Session, document_id: uuid.UUID, status: str, *, error: str | None = None
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_flashcards (document_id, status, error, updated_at)
            VALUES (:id, :status, :error, now())
            ON CONFLICT (document_id)
            DO UPDATE SET status = EXCLUDED.status, error = EXCLUDED.error, updated_at = now()
            """
        ),
        {"id": document_id, "status": status, "error": error},
    )


def save_flashcards(db: Session, document_id: uuid.UUID, cards: list[dict[str, str]]) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_flashcards (document_id, cards, status, error, updated_at)
            VALUES (:id, CAST(:cards AS jsonb), 'ready', NULL, now())
            ON CONFLICT (document_id)
            DO UPDATE SET cards = EXCLUDED.cards, status = 'ready', error = NULL, updated_at = now()
            """
        ),
        {"id": document_id, "cards": json.dumps(cards)},
    )


def ensure_flashcards(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    state = load_flashcards(db, document_id)
    if state["status"] in ("ready", "generating"):
        return state
    from app.services.jobs import enqueue_flashcards

    _set_flashcards_status(db, document_id, "generating")
    db.commit()
    enqueue_flashcards(db, document_id)
    return {"status": "generating", "cards": [], "error": None}


def run_flashcards_generation(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Worker entry: build and persist the flashcard deck for a document."""
    import asyncio

    from app.graphs.flashcards_graph import generate_flashcards

    _set_flashcards_status(db, document_id, "generating")
    db.commit()
    try:
        cards = asyncio.run(generate_flashcards(db, document_id))
    except Exception as exc:
        _set_flashcards_status(db, document_id, "failed", error=str(exc)[:500])
        db.commit()
        raise
    if cards:
        save_flashcards(db, document_id, cards)
    else:
        _set_flashcards_status(db, document_id, "failed", error="no_cards_generated")
    db.commit()
    return cards
