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

from app.services.owner_scope import note_owner_scope, owner_scope_sql
from app.services.session_design import plan_learner_list_cap


def list_notes(
    db: Session,
    document_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None = None,
) -> list[dict[str, Any]]:
    uid, gid = note_owner_scope(account_id, guest_id)
    rows = db.execute(
        text(
            f"SELECT id, content, quote, created_at FROM qb.document_saved_notes "
            f"WHERE {owner_scope_sql()} "
            "ORDER BY created_at DESC LIMIT :lim"
        ),
        {"d": document_id, "uid": uid, "gid": gid, "lim": plan_learner_list_cap("saved_notes")},
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
    guest_id: str | None = None,
) -> dict[str, Any]:
    uid, gid = note_owner_scope(account_id, guest_id)
    note_id = uuid.uuid4()
    row = db.execute(
        text(
            """
            INSERT INTO qb.document_saved_notes (id, document_id, account_id, guest_id, content, quote)
            VALUES (:id, :d, :uid, :gid, :content, :quote)
            RETURNING id, content, quote, created_at
            """
        ),
        {"id": note_id, "d": document_id, "uid": uid, "gid": gid, "content": content, "quote": quote},
    ).mappings().first()
    db.commit()
    return {
        "id": str(row["id"]),
        "content": row["content"],
        "quote": row["quote"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def delete_note(
    db: Session,
    document_id: uuid.UUID,
    note_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None = None,
) -> bool:
    uid, gid = note_owner_scope(account_id, guest_id)
    res = db.execute(
        text(
            f"DELETE FROM qb.document_saved_notes "
            f"WHERE id = :id AND {owner_scope_sql()}"
        ),
        {"id": note_id, "d": document_id, "uid": uid, "gid": gid},
    )
    db.commit()
    return res.rowcount > 0
