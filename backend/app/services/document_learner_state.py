"""Per-learner learn progress for shared documents (newspaper editions)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, first_match, pick

logger = logging.getLogger(__name__)

_KEY_RULES = (
    Rule(when=(Pred("has_user", "truthy"),), action="user"),
    Rule(when=(Pred("has_guest", "truthy"),), action="guest"),
    Rule(when=(), action="none"),
)
_PROGRESS_RULES = (
    Rule(when=(Pred("is_none", "truthy"),), action="none"),
    Rule(when=(Pred("is_dict", "truthy"),), action="dict"),
    Rule(when=(Pred("is_str", "truthy"),), action="str"),
    Rule(when=(), action="mapping"),
)


def learner_key_for_user(user_id: uuid.UUID | None, guest_id: str | None) -> str | None:
    hit = first_match(
        _KEY_RULES,
        {"has_user": user_id is not None, "has_guest": bool(guest_id)},
    )
    return apply(
        hit.action,
        {
            "user": lambda: f"user:{user_id}",
            "guest": lambda: f"guest:{guest_id}",
            "none": lambda: None,
        },
    )


def document_uses_learner_overlay(doc: Any) -> bool:
    """True when multiple learners can study the same document independently."""
    meta = getattr(doc, "meta", None) or {}
    account_id = getattr(doc, "account_id", None)
    return bool(
        meta.get("newspaper")
        or meta.get("hide_source")
        or meta.get("ingest_kind") == "newspaper"
        or (account_id is None and (meta.get("is_demo") or meta.get("is_public")))
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
    return _coerce_progress(row)


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

    def _load() -> dict[uuid.UUID, dict[str, Any]]:
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
            progress = pick(
                isinstance(row["progress"], str),
                lambda: json.loads(row["progress"]),
                lambda: row["progress"],
            )
            pick(
                isinstance(progress, dict),
                lambda: out.__setitem__(row["document_id"], progress),
                lambda: None,
            )
        return out

    return pick(not document_ids, lambda: {}, _load)
