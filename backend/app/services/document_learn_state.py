"""Dedicated learn-progress storage — avoids rewriting full documents.meta blobs."""

from __future__ import annotations
import logging

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, first_match


logger = logging.getLogger(__name__)

_PROGRESS_RULES = (
    Rule(when=(Pred("is_none", "truthy"),), action="none"),
    Rule(when=(Pred("is_dict", "truthy"),), action="dict"),
    Rule(when=(Pred("is_str", "truthy"),), action="str"),
    Rule(when=(), action="mapping"),
)


def _coerce_progress(row: Any) -> dict[str, Any] | None:
    hit = first_match(
        _PROGRESS_RULES,
        {
            "is_none": row is None,
            "is_dict": isinstance(row, dict),
            "is_str": isinstance(row, str),
        },
    )
    return apply(
        hit.action,
        {
            "none": lambda: None,
            "dict": lambda: row,
            "str": lambda: json.loads(row),
            "mapping": lambda: dict(row),
        },
    )


def load_progress(db: Session, document_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text("SELECT progress FROM qb.document_learn_state WHERE document_id = :id"),
        {"id": document_id},
    ).scalar()
    return _coerce_progress(row)


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
