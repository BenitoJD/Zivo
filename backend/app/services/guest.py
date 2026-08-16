"""Anonymous guest sessions — documents and chat without sign-in."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import pick
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
_KNOWN_KEYS = set(_LEARNER_LIST_KEYS) | set(_LEARNER_MAX_KEYS) | set(_LEARNER_BOOL_KEYS)


def ensure_guest_id(response: Response | None, cookie_id: str | None) -> str:
    return ensure_demo_cookie(response, cookie_id)


def guest_id_from_cookie(cookie_id: str | None) -> str | None:
    return cookie_id or None


def claim_guest_documents(db: Session, account_id: uuid.UUID, guest_id: str | None) -> list[uuid.UUID]:
    """Attach anonymous uploads to the account that just signed in."""

    def _claim() -> list[uuid.UUID]:
        docs = (
            db.query(Document)
            .filter(Document.account_id.is_(None))
            .filter(Document.meta["guest_id"].astext == guest_id)
            .all()
        )

        def _apply() -> list[uuid.UUID]:
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

        return pick(not docs, lambda: [], _apply)

    return pick(not guest_id, lambda: [], _claim)


def _merge_learner_progress_rows(
    guest_progress: dict[str, Any], user_progress: dict[str, Any]
) -> dict[str, Any]:
    """Union guest progress into the signed-in learner row without double-counting ids."""
    merged = dict(user_progress)
    for key in _LEARNER_LIST_KEYS:
        guest_items = [str(x) for x in (guest_progress.get(key) or [])]
        user_items = [str(x) for x in (merged.get(key) or [])]
        seen = set(user_items)
        merged[key] = user_items + list(filter(lambda item: item not in seen, guest_items))
    for key in _LEARNER_MAX_KEYS:
        merged[key] = max(
            int(guest_progress.get(key) or 0),
            int(merged.get(key) or 0),
        )
    for key in _LEARNER_BOOL_KEYS:
        merged[key] = bool(guest_progress.get(key)) or bool(merged.get(key))
    for key, value in guest_progress.items():
        pick(
            key in _KNOWN_KEYS,
            lambda: None,
            lambda: pick(
                key not in merged or merged[key] in (None, "", [], {}),
                lambda: merged.__setitem__(key, value),
                lambda: None,
            ),
        )
    return merged


def _coerce_progress(raw: Any) -> dict[str, Any]:
    loaded = pick(isinstance(raw, str), lambda: json.loads(raw), lambda: raw)
    return pick(isinstance(loaded, dict), lambda: loaded, lambda: {})


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

    def _merge_all() -> int:
        merged_count = 0
        for row in guest_rows:
            document_id = row["document_id"]
            guest_progress = _coerce_progress(row["progress"])
            user_row = db.execute(
                text(
                    """
                    SELECT progress FROM qb.document_learner_state
                    WHERE document_id = :doc AND learner_key = :user_key
                    """
                ),
                {"doc": document_id, "user_key": user_key},
            ).scalar()

            def _rename() -> None:
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

            def _combine() -> None:
                merged = _merge_learner_progress_rows(guest_progress, _coerce_progress(user_row))
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

            pick(user_row is None, _rename, _combine)
            merged_count += 1
        return merged_count

    return pick(not guest_rows, lambda: 0, _merge_all)


def _claim_guest_measurements(
    db: Session,
    *,
    guest_entity_id: uuid.UUID,
    account_entity_id: uuid.UUID,
) -> int:
    """Re-attribute a guest's answer measurements to their new account entity."""
    inserted = db.execute(
        text(
            """
            INSERT INTO intel.measurement (
                metric_concept_id, subject_entity_id, source_assertion_id,
                observed_at, value_numeric, value_text, value_json, unit,
                artifact_id, artifact_captured_at, activity_id, confidence
            )
            SELECT
                m.metric_concept_id, :account_entity, m.source_assertion_id,
                m.observed_at, m.value_numeric, m.value_text, m.value_json, m.unit,
                m.artifact_id, m.artifact_captured_at, m.activity_id, m.confidence
            FROM intel.measurement m
            WHERE m.subject_entity_id = :guest_entity
            ON CONFLICT (subject_entity_id, source_assertion_id, metric_concept_id)
                WHERE subject_entity_id IS NOT NULL AND source_assertion_id IS NOT NULL
              DO NOTHING
            RETURNING 1
            """
        ),
        {"guest_entity": guest_entity_id, "account_entity": account_entity_id},
    ).fetchall()
    return len(inserted)


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

    def _empty() -> dict[str, int]:
        return {
            "learner_state_rows": 0,
            "measurements_moved": 0,
            "sd_sessions_moved": 0,
            "notes_claimed": 0,
            "interviews_claimed": 0,
        }

    def _claim() -> dict[str, int]:
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

    return pick(not guest_id, _empty, _claim)


def document_owned_by_guest(doc: Document, guest_id: str | None) -> bool:
    return pick(
        doc.account_id is not None,
        lambda: False,
        lambda: pick(
            bool(doc.meta and (doc.meta.get("is_demo") or doc.meta.get("is_public"))),
            lambda: False,
            lambda: bool(guest_id) and doc.meta.get("guest_id") == guest_id,
        ),
    )


def can_access_document(doc: Document, user: Account | None, guest_id: str | None) -> bool:
    return pick(
        doc.account_id is not None,
        lambda: user is not None and doc.account_id == user.id,
        lambda: pick(
            bool(doc.meta and (doc.meta.get("is_demo") or doc.meta.get("is_public"))),
            lambda: True,
            lambda: document_owned_by_guest(doc, guest_id),
        ),
    )
