"""Saved-notes storage for Read / Study-Buddy mode.

An append-only list of notes per document (a saved answer + the passage it came from),
kept linked to the document. Raw parameterized SQL on qb.document_saved_notes.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

_MAX_NOTES = 500


def list_notes(db: Session, document_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT id, content, quote, created_at FROM qb.document_saved_notes "
            "WHERE document_id = :d ORDER BY created_at DESC LIMIT :lim"
        ),
        {"d": document_id, "lim": _MAX_NOTES},
    ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "content": r["content"],
            "quote": r["quote"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


def add_note(
    db: Session, document_id: uuid.UUID, *, content: str, quote: str | None = None
) -> dict[str, Any]:
    note_id = uuid.uuid4()
    row = db.execute(
        text(
            """
            INSERT INTO qb.document_saved_notes (id, document_id, content, quote)
            VALUES (:id, :d, :content, :quote)
            RETURNING id, content, quote, created_at
            """
        ),
        {"id": note_id, "d": document_id, "content": content, "quote": quote},
    ).mappings().first()
    db.commit()
    return {
        "id": str(row["id"]),
        "content": row["content"],
        "quote": row["quote"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def delete_note(db: Session, document_id: uuid.UUID, note_id: uuid.UUID) -> bool:
    res = db.execute(
        text("DELETE FROM qb.document_saved_notes WHERE id = :id AND document_id = :d"),
        {"id": note_id, "d": document_id},
    )
    db.commit()
    return res.rowcount > 0
