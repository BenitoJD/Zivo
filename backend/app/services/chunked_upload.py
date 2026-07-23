"""Resumable multipart uploads via MinIO + qb.upload_session."""

from __future__ import annotations
import logging

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account
from app.services.document_create import assert_storage_available, create_document_from_storage
from app.services.storage import (
    abort_multipart_upload,
    complete_multipart_upload,
    start_multipart_upload,
    upload_multipart_part,
    _safe_storage_filename,
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
            FROM qb.upload_session
            WHERE id = :id
            """
        ),
        {"id": session_id},
    ).mappings().first()
    return dict(row) if row else None


def _assert_session_access(
    row: dict[str, Any] | None,
    *,
    user: Account | None,
    guest_id: str | None,
) -> dict[str, Any]:
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    if row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Upload session expired")
    if user:
        if row["account_id"] != user.id:
            raise HTTPException(status_code=404, detail="Upload session not found")
    elif guest_id:
        if row["guest_id"] != guest_id:
            raise HTTPException(status_code=404, detail="Upload session not found")
    else:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return row


def init_upload_session(
    db: Session,
    *,
    user: Account | None,
    guest_id: str | None,
    filename: str,
    content_type: str,
    total_size: int,
    chunk_size: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if total_size < 1 or total_size > settings.max_upload_bytes:
        limit_gb = settings.max_upload_bytes // (1024 * 1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File too large (max {limit_gb}GB)")

    size = chunk_size or DEFAULT_CHUNK_SIZE
    size = max(256 * 1024, min(size, 32 * 1024 * 1024))

    is_image = content_type.startswith("image/")
    doc_guest_id = assert_storage_available(
        db,
        user=user,
        guest_id=guest_id,
        size_bytes=total_size,
        counts_toward_guest_cap=not is_image,
    )

    prefix = f"users/{user.id}" if user else "demo"
    storage_key = f"{prefix}/{uuid.uuid4()}/{_safe_storage_filename(filename)}"
    upload_id = start_multipart_upload(storage_key, content_type)

    session_id = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    db.execute(
        text(
            """
            INSERT INTO qb.upload_session (
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
            "account_id": user.id if user else None,
            "guest_id": doc_guest_id,
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
    user: Account | None,
    guest_id: str | None,
) -> dict[str, Any]:
    row = _assert_session_access(_session_row(db, session_id), user=user, guest_id=guest_id)
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
    user: Account | None,
    guest_id: str | None,
) -> dict[str, Any]:
    if part_number < 1:
        raise HTTPException(status_code=400, detail="Invalid part number")
    row = _assert_session_access(_session_row(db, session_id), user=user, guest_id=guest_id)
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
            UPDATE qb.upload_session
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
    user: Account | None,
    guest_id: str | None,
):
    # Lock the session row so concurrent complete calls cannot both create documents.
    locked = db.execute(
        text(
            """
            SELECT id, account_id, guest_id, filename, content_type, total_size,
                   chunk_size, storage_key, multipart_upload_id, parts, expires_at
            FROM qb.upload_session
            WHERE id = :id
            FOR UPDATE
            """
        ),
        {"id": session_id},
    ).mappings().first()
    row = _assert_session_access(dict(locked) if locked else None, user=user, guest_id=guest_id)
    parts = row["parts"] if isinstance(row["parts"], dict) else json.loads(row["parts"] or "{}")
    expected = math.ceil(int(row["total_size"]) / int(row["chunk_size"]))
    if len(parts) != expected:
        raise HTTPException(status_code=400, detail="Missing upload parts")

    part_list = [
        {"PartNumber": int(num), "ETag": etag}
        for num, etag in sorted(parts.items(), key=lambda item: int(item[0]))
    ]
    complete_multipart_upload(row["storage_key"], row["multipart_upload_id"], part_list)

    doc = create_document_from_storage(
        db,
        user=user,
        guest_id=guest_id,
        filename=row["filename"],
        content_type=row["content_type"],
        storage_key=row["storage_key"],
        size_bytes=int(row["total_size"]),
        guest_id_override=row["guest_id"],
    )
    db.execute(text("DELETE FROM qb.upload_session WHERE id = :id"), {"id": session_id})
    db.commit()
    return doc


def abort_upload_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    user: Account | None,
    guest_id: str | None,
) -> None:
    row = _assert_session_access(_session_row(db, session_id), user=user, guest_id=guest_id)
    try:
        abort_multipart_upload(row["storage_key"], row["multipart_upload_id"])
    except Exception:
        logger.debug("multipart upload abort failed", exc_info=True)
    db.execute(text("DELETE FROM qb.upload_session WHERE id = :id"), {"id": session_id})
    db.commit()
