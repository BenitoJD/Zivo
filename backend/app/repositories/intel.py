"""Intel schema repositories (raw SQL)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _concept_id(db: Session, uri: str) -> uuid.UUID:
    row = db.execute(
        text("SELECT id FROM intel.concept WHERE uri = :uri"),
        {"uri": uri},
    ).first()
    if not row:
        raise ValueError(f"Missing concept seed: {uri}")
    return row[0]


def _source_id(db: Session, slug: str) -> uuid.UUID:
    row = db.execute(
        text("SELECT id FROM intel.source WHERE slug = :slug"),
        {"slug": slug},
    ).first()
    if not row:
        raise ValueError(f"Missing source seed: {slug}")
    return row[0]


def create_activity(
    db: Session,
    *,
    type_uri: str,
    agent: str,
    source_slug: str | None = None,
    stats: dict[str, Any] | None = None,
) -> uuid.UUID:
    activity_id = uuid.uuid4()
    source_id = _source_id(db, source_slug) if source_slug else None
    db.execute(
        text(
            """
            INSERT INTO intel.activity (id, type_concept_id, source_id, agent, status, stats)
            VALUES (:id, :type_id, :source_id, :agent, 'running', CAST(:stats AS jsonb))
            """
        ),
        {
            "id": activity_id,
            "type_id": _concept_id(db, type_uri),
            "source_id": source_id,
            "agent": agent,
            "stats": json.dumps(stats or {}),
        },
    )
    return activity_id


def update_activity(
    db: Session,
    activity_id: uuid.UUID,
    *,
    status: str | None = None,
    stats: dict[str, Any] | None = None,
    error_summary: str | None = None,
    finished: bool = False,
) -> None:
    parts = []
    params: dict[str, Any] = {"id": activity_id}
    if status:
        parts.append("status = :status")
        params["status"] = status
    if stats is not None:
        parts.append("stats = stats || CAST(:stats AS jsonb)")
        params["stats"] = json.dumps(stats)
    if error_summary is not None:
        parts.append("error_summary = :error")
        params["error"] = error_summary
    if finished:
        parts.append("finished_at = :finished_at")
        params["finished_at"] = datetime.now(timezone.utc)
    if not parts:
        return
    db.execute(
        text(f"UPDATE intel.activity SET {', '.join(parts)} WHERE id = :id"),
        params,
    )


def insert_artifact(
    db: Session,
    *,
    source_slug: str,
    external_key: str,
    content_hash: str,
    storage_uri: str,
    media_type: str,
    payload: dict[str, Any],
    byte_size: int | None = None,
    activity_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, datetime]:
    artifact_id = uuid.uuid4()
    captured_at = datetime.now(timezone.utc)
    db.execute(
        text(
            """
            INSERT INTO intel.artifact (
              id, source_id, activity_id, external_key, content_hash, byte_size,
              storage_uri, payload, media_type, captured_at
            )
            VALUES (
              :id, :source_id, :activity_id, :external_key, :content_hash, :byte_size,
              :storage_uri, CAST(:payload AS jsonb), :media_type, :captured_at
            )
            """
        ),
        {
            "id": artifact_id,
            "source_id": _source_id(db, source_slug),
            "activity_id": activity_id,
            "external_key": external_key,
            "content_hash": content_hash,
            "byte_size": byte_size,
            "storage_uri": storage_uri,
            "payload": json.dumps(payload),
            "media_type": media_type,
            "captured_at": captured_at,
        },
    )
    return artifact_id, captured_at


def get_activity(db: Session, activity_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, status, stats, error_summary, agent, started_at, finished_at
            FROM intel.activity WHERE id = :id
            """
        ),
        {"id": activity_id},
    ).mappings().first()
    return dict(row) if row else None
