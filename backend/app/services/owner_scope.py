"""Owner scope helpers for per-learner saved notes and brainstorm ideas."""

from __future__ import annotations

import uuid


def note_owner_scope(
    user_id: uuid.UUID | None, guest_id: str | None
) -> tuple[uuid.UUID | None, str | None]:
    if user_id is not None:
        return user_id, None
    if guest_id:
        return None, guest_id
    return None, None


def owner_scope_sql() -> str:
    return """
        document_id = :d AND (
            (:uid IS NOT NULL AND account_id = :uid)
            OR (:gid IS NOT NULL AND account_id IS NULL AND guest_id = :gid)
        )
    """
