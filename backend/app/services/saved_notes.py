"""Saved-notes storage for Read / Study-Buddy mode.

An append-only list of notes per document (a saved answer + the passage it came
from), kept linked to the document and scoped to the owning account.

Owner scoping (``account_id``) closes an IDOR on shared documents: when
``require_document`` grants read access to every user (``is_demo`` /
``is_public`` practice sources), only the note's author may delete or list their
own notes. ``account_id IS NULL`` rows are legacy / guest notes and stay visible
to all callers of the document. Raw parameterized SQL on qb.document_saved_notes.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

_MAX_NOTES = 500


def list_notes(
    db: Session, document_id: uuid.UUID, *, account_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    # Owner scope: a caller sees their own notes plus legacy NULL rows. They
    # never see another user's notes on a shared document.
    rows = db.execute(
        text(
            "SELECT id, content, quote, created_at FROM qb.document_saved_notes "
            "WHERE document_id = :d AND (account_id IS NULL OR account_id IS NOT DISTINCT FROM :uid) "
            "ORDER BY created_at DESC LIMIT :lim"
        ),
        {"d": document_id, "uid": account_id, "lim": _MAX_NOTES},
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
    db: Session,
    document_id: uuid.UUID,
    *,
    content: str,
    quote: str | None = None,
    account_id: uuid.UUID | None,
) -> dict[str, Any]:
    note_id = uuid.uuid4()
    row = db.execute(
        text(
            """
            INSERT INTO qb.document_saved_notes (id, document_id, account_id, content, quote)
            VALUES (:id, :d, :uid, :content, :quote)
            RETURNING id, content, quote, created_at
            """
        ),
        {"id": note_id, "d": document_id, "uid": account_id, "content": content, "quote": quote},
    ).mappings().first()
    db.commit()
    return {
        "id": str(row["id"]),
        "content": row["content"],
        "quote": row["quote"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def delete_note(
    db: Session, document_id: uuid.UUID, note_id: uuid.UUID, *, account_id: uuid.UUID | None
) -> bool:
    # A caller may only delete notes they authored (``account_id`` matches) or
    # legacy NULL-owned notes — never another user's notes on a shared document.
    res = db.execute(
        text(
            "DELETE FROM qb.document_saved_notes "
            "WHERE id = :id AND document_id = :d "
            "AND (account_id IS NULL OR account_id IS NOT DISTINCT FROM :uid)"
        ),
        {"id": note_id, "d": document_id, "uid": account_id},
    )
    db.commit()
    return res.rowcount > 0
