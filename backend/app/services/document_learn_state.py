"""Dedicated learn-progress storage — avoids rewriting full documents.meta blobs."""

from __future__ import annotations
import logging

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

def load_progress(db: Session, document_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text("SELECT progress FROM qb.document_learn_state WHERE document_id = :id"),
        {"id": document_id},
    ).scalar()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if isinstance(row, str):
        return json.loads(row)
    return dict(row)


def save_progress_row(db: Session, document_id: uuid.UUID, progress: dict[str, Any]) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_learn_state (document_id, progress, updated_at)
            VALUES (:id, CAST(:progress AS jsonb), now())
            ON CONFLICT (document_id) DO UPDATE
            SET progress = EXCLUDED.progress, updated_at = now()
            """
        ),
        {"id": document_id, "progress": json.dumps(progress)},
    )
    try:
        db.execute(
            text("SELECT pg_notify(:channel, '')"),
            {"channel": f"zivo_learn_{document_id}"},
        )
    except Exception:
        logger.debug("learn-state pg_notify failed", exc_info=True)
