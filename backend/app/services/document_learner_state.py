"""Per-learner learn progress for shared documents (newspaper editions)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def learner_key_for_user(user_id: uuid.UUID | None, guest_id: str | None) -> str | None:
    if user_id is not None:
        return f"user:{user_id}"
    if guest_id:
        return f"guest:{guest_id}"
    return None


def document_uses_learner_overlay(doc: Any) -> bool:
    """True when multiple learners can study the same document independently."""
    meta = getattr(doc, "meta", None) or {}
    account_id = getattr(doc, "account_id", None)
    if meta.get("newspaper") or meta.get("hide_source") or meta.get("ingest_kind") == "newspaper":
        return True
    if account_id is None and (meta.get("is_demo") or meta.get("is_public")):
        return True
    return False


def load_learner_progress(
    db: Session, document_id: uuid.UUID, learner_key: str
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT progress FROM qb.document_learner_state
            WHERE document_id = :doc AND learner_key = :key
            """
        ),
        {"doc": document_id, "key": learner_key},
    ).scalar()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if isinstance(row, str):
        return json.loads(row)
    return dict(row)


def save_learner_progress_row(
    db: Session,
    document_id: uuid.UUID,
    learner_key: str,
    progress: dict[str, Any],
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_learner_state (document_id, learner_key, progress, updated_at)
            VALUES (:doc, :key, CAST(:progress AS jsonb), now())
            ON CONFLICT (document_id, learner_key) DO UPDATE
            SET progress = EXCLUDED.progress, updated_at = now()
            """
        ),
        {"doc": document_id, "key": learner_key, "progress": json.dumps(progress)},
    )
    try:
        db.execute(
            text("SELECT pg_notify(:channel, '')"),
            {"channel": f"zivo_learn_{document_id}"},
        )
    except Exception:
        logger.debug("learner-state pg_notify failed", exc_info=True)


def load_learner_progress_batch(
    db: Session, document_ids: list[uuid.UUID], learner_key: str
) -> dict[uuid.UUID, dict[str, Any]]:
    """Load per-document learner progress for many shared documents at once."""
    if not document_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT document_id, progress
            FROM qb.document_learner_state
            WHERE learner_key = :key
              AND document_id = ANY(:docs)
            """
        ),
        {"key": learner_key, "docs": document_ids},
    ).mappings().all()
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        progress = row["progress"]
        if isinstance(progress, str):
            progress = json.loads(progress)
        if isinstance(progress, dict):
            out[row["document_id"]] = progress
    return out
