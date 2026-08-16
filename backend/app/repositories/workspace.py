"""Mutable per-account artifact workspace state."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import pick


def upsert_workspace(
    db: Session,
    *,
    account_id: uuid.UUID | None,
    artifact_id: uuid.UUID,
    artifact_captured_at: datetime,
    **fields: Any,
) -> None:
    def _upsert() -> None:
        allowed = {
            "status",
            "page_count",
            "selected_range",
            "current_page",
            "unlocked_through_page",
            "pool_target",
            "pool_available_count",
        }
        cols = ["account_id", "artifact_id", "artifact_captured_at"]
        vals = [":account_id", ":artifact_id", ":artifact_captured_at"]
        params: dict[str, Any] = {
            "account_id": account_id,
            "artifact_id": artifact_id,
            "artifact_captured_at": artifact_captured_at,
        }
        updates = ["updated_at = now()"]
        for key, value in filter(lambda kv: kv[0] in allowed, fields.items()):
            cols.append(key)
            vals.append(f":{key}")
            params[key] = pick(
                key == "selected_range" and isinstance(value, dict),
                lambda: json.dumps(value),
                lambda: value,
            )
            updates.append(f"{key} = EXCLUDED.{key}")
        db.execute(
            text(
                f"""
                INSERT INTO qb.artifact_workspace ({', '.join(cols)})
                VALUES ({', '.join(vals)})
                ON CONFLICT (account_id, artifact_id, artifact_captured_at)
                DO UPDATE SET {', '.join(updates)}
                """
            ),
            params,
        )

    pick(account_id is None, lambda: None, _upsert)


def get_workspace(
    db: Session,
    account_id: uuid.UUID,
    artifact_id: uuid.UUID,
    artifact_captured_at: datetime,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT * FROM qb.artifact_workspace
            WHERE account_id = :account_id
              AND artifact_id = :artifact_id
              AND artifact_captured_at = :captured_at
            """
        ),
        {
            "account_id": account_id,
            "artifact_id": artifact_id,
            "captured_at": artifact_captured_at,
        },
    ).mappings().first()
    return pick(bool(row), lambda: dict(row), lambda: None)
