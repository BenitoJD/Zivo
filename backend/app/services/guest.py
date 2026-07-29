"""Anonymous guest sessions — documents and chat without sign-in."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Account, ChatThread, Document
from app.services.usage import DEMO_COOKIE, ensure_demo_cookie

__all__ = [
    "DEMO_COOKIE",
    "can_access_document",
    "claim_guest_documents",
    "claim_guest_progress",
    "document_owned_by_guest",
    "ensure_guest_id",
    "guest_id_from_cookie",
]

_LEARNER_LIST_KEYS = (
    "answered_ids",
    "learn_answered_ids",
    "test_answered_ids",
)
_LEARNER_MAX_KEYS = (
    "session_items_answered",
    "answered_on_page",
    "generated_on_page",
)
_LEARNER_BOOL_KEYS = (
    "learn_complete",
    "mastery_stop",
)


def ensure_guest_id(response: Response | None, cookie_id: str | None) -> str:
    return ensure_demo_cookie(response, cookie_id)


def guest_id_from_cookie(cookie_id: str | None) -> str | None:
    return cookie_id or None


def claim_guest_documents(db: Session, account_id: uuid.UUID, guest_id: str | None) -> list[uuid.UUID]:
    """Attach anonymous uploads to the account that just signed in."""
    if not guest_id:
        return []

    docs = (
        db.query(Document)
        .filter(Document.account_id.is_(None))
        .filter(Document.meta["guest_id"].astext == guest_id)
        .all()
    )
    if not docs:
        return []

    claimed: list[uuid.UUID] = []
    for doc in docs:
        doc.account_id = account_id
        meta = dict(doc.meta or {})
        meta.pop("guest_id", None)
        doc.meta = meta
        claimed.append(doc.id)

        db.query(ChatThread).filter(
            ChatThread.artifact_id == (doc.artifact_id or doc.id),
            ChatThread.account_id.is_(None),
        ).update({ChatThread.account_id: account_id}, synchronize_session=False)

    db.commit()
    return claimed


def _merge_learner_progress_rows(
    guest_progress: dict[str, Any], user_progress: dict[str, Any]
) -> dict[str, Any]:
    """Union guest progress into the signed-in learner row without double-counting ids."""
    merged = dict(user_progress)
    for key in _LEARNER_LIST_KEYS:
        guest_items = [str(x) for x in (guest_progress.get(key) or [])]
        user_items = [str(x) for x in (merged.get(key) or [])]
        seen = set(user_items)
        merged[key] = user_items + [item for item in guest_items if item not in seen]
    for key in _LEARNER_MAX_KEYS:
        merged[key] = max(
            int(guest_progress.get(key) or 0),
            int(merged.get(key) or 0),
        )
    for key in _LEARNER_BOOL_KEYS:
        merged[key] = bool(guest_progress.get(key)) or bool(merged.get(key))
    for key, value in guest_progress.items():
        if key in _LEARNER_LIST_KEYS or key in _LEARNER_MAX_KEYS or key in _LEARNER_BOOL_KEYS:
            continue
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    return merged


def _claim_guest_learner_state(
    db: Session, *, guest_key: str, user_key: str
) -> int:
    guest_rows = db.execute(
        text(
            """
            SELECT document_id, progress
            FROM qb.document_learner_state
            WHERE learner_key = :guest_key
            """
        ),
        {"guest_key": guest_key},
    ).mappings().all()
    if not guest_rows:
        return 0

    merged_count = 0
    for row in guest_rows:
        document_id = row["document_id"]
        guest_progress = row["progress"]
        if isinstance(guest_progress, str):
            guest_progress = json.loads(guest_progress)
        if not isinstance(guest_progress, dict):
            guest_progress = {}

        user_row = db.execute(
            text(
                """
                SELECT progress FROM qb.document_learner_state
                WHERE document_id = :doc AND learner_key = :user_key
                """
            ),
            {"doc": document_id, "user_key": user_key},
        ).scalar()

        if user_row is None:
            db.execute(
                text(
                    """
                    UPDATE qb.document_learner_state
                    SET learner_key = :user_key, updated_at = now()
                    WHERE document_id = :doc AND learner_key = :guest_key
                    """
                ),
                {"doc": document_id, "guest_key": guest_key, "user_key": user_key},
            )
            merged_count += 1
            continue

        user_progress = user_row
        if isinstance(user_progress, str):
            user_progress = json.loads(user_progress)
        if not isinstance(user_progress, dict):
            user_progress = {}

        merged = _merge_learner_progress_rows(guest_progress, user_progress)
        db.execute(
            text(
                """
                UPDATE qb.document_learner_state
                SET progress = CAST(:progress AS jsonb), updated_at = now()
                WHERE document_id = :doc AND learner_key = :user_key
                """
            ),
            {"doc": document_id, "user_key": user_key, "progress": json.dumps(merged)},
        )
        db.execute(
            text(
                """
                DELETE FROM qb.document_learner_state
                WHERE document_id = :doc AND learner_key = :guest_key
                """
            ),
            {"doc": document_id, "guest_key": guest_key},
        )
        merged_count += 1
    return merged_count


def _claim_guest_measurements(
    db: Session,
    *,
    guest_entity_id: uuid.UUID,
    account_entity_id: uuid.UUID,
) -> int:
    moved = db.execute(
        text(
            """
            UPDATE intel.measurement m
            SET subject_entity_id = :account_entity
            WHERE subject_entity_id = :guest_entity
              AND NOT EXISTS (
                SELECT 1 FROM intel.measurement existing
                WHERE existing.subject_entity_id = :account_entity
                  AND existing.source_assertion_id = m.source_assertion_id
                  AND existing.metric_concept_id = m.metric_concept_id
              )
            """
        ),
        {"guest_entity": guest_entity_id, "account_entity": account_entity_id},
    ).rowcount
    db.execute(
        text("DELETE FROM intel.measurement WHERE subject_entity_id = :guest_entity"),
        {"guest_entity": guest_entity_id},
    )
    return int(moved or 0)


def _claim_guest_sd_sessions(db: Session, *, account_id: uuid.UUID, guest_id: str) -> int:
    return int(
        db.execute(
            text(
                """
                UPDATE qb.sd_session
                SET account_id = :account_id, guest_id = NULL, updated_at = now()
                WHERE guest_id = :guest_id AND account_id IS NULL
                """
            ),
            {"account_id": account_id, "guest_id": guest_id},
        ).rowcount
        or 0
    )


def _claim_guest_notes(db: Session, *, guest_id: str, account_id: uuid.UUID) -> int:
    notes = db.execute(
        text(
            """
            UPDATE qb.document_saved_notes
            SET account_id = :uid, guest_id = NULL
            WHERE guest_id = :gid
            """
        ),
        {"uid": account_id, "gid": guest_id},
    ).rowcount or 0
    ideas = db.execute(
        text(
            """
            UPDATE qb.document_brainstorm_ideas
            SET account_id = :uid, guest_id = NULL
            WHERE guest_id = :gid
            """
        ),
        {"uid": account_id, "gid": guest_id},
    ).rowcount or 0
    return int(notes) + int(ideas)


def _claim_guest_interviews(db: Session, *, guest_key: str, user_key: str) -> int:
    db.execute(
        text(
            """
            DELETE FROM qb.document_interview
            WHERE learner_key = :user_key
              AND document_id IN (
                SELECT document_id FROM qb.document_interview WHERE learner_key = :guest_key
              )
            """
        ),
        {"guest_key": guest_key, "user_key": user_key},
    )
    result = db.execute(
        text(
            """
            UPDATE qb.document_interview
            SET learner_key = :user_key, updated_at = now()
            WHERE learner_key = :guest_key
            """
        ),
        {"guest_key": guest_key, "user_key": user_key},
    )
    return int(result.rowcount or 0)


def claim_guest_progress(
    db: Session,
    account_id: uuid.UUID,
    username: str,
    guest_id: str | None,
) -> dict[str, int]:
    """Merge anonymous practice progress into the account that just signed in."""
    if not guest_id:
        return {
            "learner_state_rows": 0,
            "measurements_moved": 0,
            "sd_sessions_moved": 0,
            "notes_claimed": 0,
            "interviews_claimed": 0,
        }

    from app.repositories.intel import get_or_create_account_entity, get_or_create_concept_entity

    guest_key = f"guest:{guest_id}"
    user_key = f"user:{account_id}"

    learner_state_rows = _claim_guest_learner_state(db, guest_key=guest_key, user_key=user_key)

    guest_entity_id = get_or_create_concept_entity(
        db, f"guest:{guest_id}", f"Guest {guest_id[:8]}"
    )
    account_entity_id = get_or_create_account_entity(db, account_id, username)
    measurements_moved = _claim_guest_measurements(
        db,
        guest_entity_id=guest_entity_id,
        account_entity_id=account_entity_id,
    )
    sd_sessions_moved = _claim_guest_sd_sessions(db, account_id=account_id, guest_id=guest_id)
    notes_claimed = _claim_guest_notes(db, guest_id=guest_id, account_id=account_id)
    interviews_claimed = _claim_guest_interviews(db, guest_key=guest_key, user_key=user_key)

    db.commit()
    return {
        "learner_state_rows": learner_state_rows,
        "measurements_moved": measurements_moved,
        "sd_sessions_moved": sd_sessions_moved,
        "notes_claimed": notes_claimed,
        "interviews_claimed": interviews_claimed,
    }


def document_owned_by_guest(doc: Document, guest_id: str | None) -> bool:
    if doc.account_id is not None:
        return False
    if doc.meta and (doc.meta.get("is_demo") or doc.meta.get("is_public")):
        return False
    return bool(guest_id) and doc.meta.get("guest_id") == guest_id


def can_access_document(doc: Document, user: Account | None, guest_id: str | None) -> bool:
    if doc.account_id is not None:
        return user is not None and doc.account_id == user.id
    if doc.meta and (doc.meta.get("is_demo") or doc.meta.get("is_public")):
        # Platform-owned documents: open to everyone.
        return True
    return document_owned_by_guest(doc, guest_id)
