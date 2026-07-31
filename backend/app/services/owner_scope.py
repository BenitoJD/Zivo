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
    # Both :uid and :gid are always bound by callers, and exactly one is NULL
    # (see note_owner_scope). With psycopg3, a NULL bound param is sent as an
    # untyped NULL, and Postgres cannot infer its type from ``:uid IS NOT NULL``
    # alone — it raised ``could not determine data type of parameter $N`` on
    # every document open (surfaced as a 503 "Database unavailable").
    #
    # The CASTs give each NULL a concrete type; ``IS NOT DISTINCT FROM`` keeps
    # the equality NULL-safe (so a logged-in user's non-NULL account_id still
    # matches, and the guest branch still matches the NULL account_id rows).
    return """
        document_id = :d AND (
            account_id IS NOT DISTINCT FROM CAST(:uid AS uuid)
            OR (account_id IS NULL AND guest_id IS NOT DISTINCT FROM CAST(:gid AS text))
        )
    """
