"""Mutable per-account artifact workspace state."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def upsert_workspace(
    db: Session,
    *,
    account_id: uuid.UUID | None,
    artifact_id: uuid.UUID,
    artifact_captured_at: datetime,
    **fields: Any,
) -> None:
    if account_id is None:
        return
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
    for key, value in fields.items():
        if key not in allowed:
            continue
        cols.append(key)
        vals.append(f":{key}")
        params[key] = json.dumps(value) if key == "selected_range" and isinstance(value, dict) else value
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
    return dict(row) if row else None
