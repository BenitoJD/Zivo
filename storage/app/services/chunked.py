"""Resumable multipart uploads via MinIO + storage.upload_session."""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.guest import Principal
from app.services.minio import (
    abort_multipart_upload,
    build_storage_key,
    complete_multipart_upload,
    start_multipart_upload,
    upload_multipart_part,
)

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024
SESSION_TTL_HOURS = 24

logger = logging.getLogger(__name__)


def _session_row(db: Session, session_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, account_id, guest_id, filename, content_type, total_size,
                   chunk_size, storage_key, multipart_upload_id, parts, expires_at
            FROM storage.upload_session
            WHERE id = :id
            """
        ),
        {"id": session_id},
    ).mappings().first()
    return dict(row) if row else None


def _assert_session_access(row: dict[str, Any] | None, principal: Principal) -> dict[str, Any]:
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    if row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Upload session expired")
    if principal.account_id:
        if row["account_id"] != principal.account_id:
            raise HTTPException(status_code=404, detail="Upload session not found")
    elif principal.guest_id:
        if row["guest_id"] != principal.guest_id:
            raise HTTPException(status_code=404, detail="Upload session not found")
    else:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return row


def init_upload_session(
    db: Session,
    *,
    principal: Principal,
    filename: str,
    content_type: str,
    total_size: int,
    chunk_size: int | None = None,
) -> dict[str, Any]:
    principal.require_identity()
    settings = get_settings()
    if total_size < 1 or total_size > settings.max_upload_bytes:
        limit_gb = settings.max_upload_bytes // (1024 * 1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File too large (max {limit_gb}GB)")

    size = chunk_size or DEFAULT_CHUNK_SIZE
    size = max(256 * 1024, min(size, 32 * 1024 * 1024))

    storage_key = build_storage_key(principal.account_id, filename)
    upload_id = start_multipart_upload(storage_key, content_type)

    session_id = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    db.execute(
        text(
            """
            INSERT INTO storage.upload_session (
              id, account_id, guest_id, filename, content_type, total_size,
              chunk_size, storage_key, multipart_upload_id, expires_at
            )
            VALUES (
              :id, :account_id, :guest_id, :filename, :content_type, :total_size,
              :chunk_size, :storage_key, :multipart_upload_id, :expires_at
            )
            """
        ),
        {
            "id": session_id,
            "account_id": principal.account_id,
            "guest_id": principal.guest_id if principal.account_id is None else None,
            "filename": filename[:512],
            "content_type": content_type,
            "total_size": total_size,
            "chunk_size": size,
            "storage_key": storage_key,
            "multipart_upload_id": upload_id,
            "expires_at": expires,
        },
    )
    db.commit()
    expected_parts = math.ceil(total_size / size)
    return {
        "session_id": str(session_id),
        "chunk_size": size,
        "expected_parts": expected_parts,
        "expires_at": expires.isoformat(),
    }


def upload_session_status(
    db: Session,
    session_id: uuid.UUID,
    *,
    principal: Principal,
) -> dict[str, Any]:
    row = _assert_session_access(_session_row(db, session_id), principal)
    parts = row["parts"] if isinstance(row["parts"], dict) else json.loads(row["parts"] or "{}")
    received = sorted(int(k) for k in parts.keys())
    expected = math.ceil(int(row["total_size"]) / int(row["chunk_size"]))
    return {
        "session_id": str(session_id),
        "received_parts": received,
        "expected_parts": expected,
        "chunk_size": int(row["chunk_size"]),
        "total_size": int(row["total_size"]),
        "filename": row["filename"],
        "content_type": row["content_type"],
    }


def upload_part(
    db: Session,
    session_id: uuid.UUID,
    part_number: int,
    data: bytes,
    *,
    principal: Principal,
) -> dict[str, Any]:
    if part_number < 1:
        raise HTTPException(status_code=400, detail="Invalid part number")
    row = _assert_session_access(_session_row(db, session_id), principal)
    chunk_size = int(row["chunk_size"])
    total_size = int(row["total_size"])
    expected_parts = math.ceil(total_size / chunk_size)
    if part_number > expected_parts:
        raise HTTPException(status_code=400, detail="Part number out of range")
    max_part = chunk_size if part_number < expected_parts else total_size - chunk_size * (expected_parts - 1)
    if len(data) > max_part or (part_number < expected_parts and len(data) != chunk_size):
        raise HTTPException(status_code=400, detail="Invalid part size")

    etag = upload_multipart_part(
        row["storage_key"],
        row["multipart_upload_id"],
        part_number,
        data,
    )
    parts = row["parts"] if isinstance(row["parts"], dict) else json.loads(row["parts"] or "{}")
    parts[str(part_number)] = etag
    db.execute(
        text(
            """
            UPDATE storage.upload_session
            SET parts = CAST(:parts AS jsonb)
            WHERE id = :id
            """
        ),
        {"id": session_id, "parts": json.dumps(parts)},
    )
    db.commit()
    return {"part_number": part_number, "etag": etag}


def complete_upload_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    principal: Principal,
) -> dict[str, Any]:
    locked = db.execute(
        text(
            """
            SELECT id, account_id, guest_id, filename, content_type, total_size,
                   chunk_size, storage_key, multipart_upload_id, parts, expires_at
            FROM storage.upload_session
            WHERE id = :id
            FOR UPDATE
            """
        ),
        {"id": session_id},
    ).mappings().first()
    row = _assert_session_access(dict(locked) if locked else None, principal)
    parts = row["parts"] if isinstance(row["parts"], dict) else json.loads(row["parts"] or "{}")
    expected = math.ceil(int(row["total_size"]) / int(row["chunk_size"]))
    if len(parts) != expected:
        raise HTTPException(status_code=400, detail="Missing upload parts")

    part_list = [
        {"PartNumber": int(num), "ETag": etag}
        for num, etag in sorted(parts.items(), key=lambda item: int(item[0]))
    ]
    complete_multipart_upload(row["storage_key"], row["multipart_upload_id"], part_list)

    object_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO storage.object (
              id, storage_key, account_id, guest_id, filename, content_type, size_bytes
            )
            VALUES (
              :id, :storage_key, :account_id, :guest_id, :filename, :content_type, :size_bytes
            )
            """
        ),
        {
            "id": object_id,
            "storage_key": row["storage_key"],
            "account_id": row["account_id"],
            "guest_id": row["guest_id"],
            "filename": row["filename"],
            "content_type": row["content_type"],
            "size_bytes": int(row["total_size"]),
        },
    )
    db.execute(text("DELETE FROM storage.upload_session WHERE id = :id"), {"id": session_id})
    db.commit()
    return {
        "object_id": str(object_id),
        "storage_key": row["storage_key"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "size_bytes": int(row["total_size"]),
        "account_id": str(row["account_id"]) if row["account_id"] else None,
        "guest_id": row["guest_id"],
    }


def abort_upload_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    principal: Principal,
) -> None:
    row = _assert_session_access(_session_row(db, session_id), principal)
    try:
        abort_multipart_upload(row["storage_key"], row["multipart_upload_id"])
    except Exception:
        logger.debug("multipart upload abort failed", exc_info=True)
    db.execute(text("DELETE FROM storage.upload_session WHERE id = :id"), {"id": session_id})
    db.commit()
