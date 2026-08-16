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
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
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


def _raise(exc: BaseException) -> None:
    raise exc


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
    return pick(row is None, lambda: None, lambda: dict(row))


def _assert_session_access(row: dict[str, Any] | None, principal: Principal) -> dict[str, Any]:
    def _missing() -> dict[str, Any]:
        _raise(HTTPException(status_code=404, detail="Upload session not found"))
        return {}

    def _after_row() -> dict[str, Any]:
        pick(
            row["expires_at"] < datetime.now(timezone.utc),
            lambda: _raise(HTTPException(status_code=410, detail="Upload session expired")),
            lambda: None,
        )

        def _account() -> dict[str, Any]:
            pick(
                row["account_id"] != principal.account_id,
                lambda: _raise(HTTPException(status_code=404, detail="Upload session not found")),
                lambda: None,
            )
            return row

        def _guest() -> dict[str, Any]:
            pick(
                row["guest_id"] != principal.guest_id,
                lambda: _raise(HTTPException(status_code=404, detail="Upload session not found")),
                lambda: None,
            )
            return row

        def _unauth() -> dict[str, Any]:
            _raise(HTTPException(status_code=401, detail="Not authenticated"))
            return {}

        return apply(
            first_match(
                (
                    Rule(when=(Pred("has_account", "truthy"),), action="account"),
                    Rule(when=(Pred("has_guest", "truthy"),), action="guest"),
                    Rule(when=(), action="unauth"),
                ),
                {
                    "has_account": bool(principal.account_id),
                    "has_guest": bool(principal.guest_id),
                },
            ).action,
            {"account": _account, "guest": _guest, "unauth": _unauth},
        )

    return pick(not row, _missing, _after_row)


def _parts_map(row: dict[str, Any]) -> dict:
    return pick(
        isinstance(row["parts"], dict),
        lambda: row["parts"],
        lambda: json.loads(row["parts"] or "{}"),
    )


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
    pick(
        total_size < 1 or total_size > settings.max_upload_bytes,
        lambda: _raise(
            HTTPException(
                status_code=413,
                detail=f"File too large (max {settings.max_upload_bytes // (1024 * 1024 * 1024)}GB)",
            )
        ),
        lambda: None,
    )

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
            "guest_id": pick(principal.account_id is None, lambda: principal.guest_id, lambda: None),
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
    parts = _parts_map(row)
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
    pick(
        part_number < 1,
        lambda: _raise(HTTPException(status_code=400, detail="Invalid part number")),
        lambda: None,
    )
    row = _assert_session_access(_session_row(db, session_id), principal)
    chunk_size = int(row["chunk_size"])
    total_size = int(row["total_size"])
    expected_parts = math.ceil(total_size / chunk_size)
    pick(
        part_number > expected_parts,
        lambda: _raise(HTTPException(status_code=400, detail="Part number out of range")),
        lambda: None,
    )
    max_part = choose(
        part_number < expected_parts,
        chunk_size,
        total_size - chunk_size * (expected_parts - 1),
    )
    pick(
        len(data) > max_part or (part_number < expected_parts and len(data) != chunk_size),
        lambda: _raise(HTTPException(status_code=400, detail="Invalid part size")),
        lambda: None,
    )

    etag = upload_multipart_part(
        row["storage_key"],
        row["multipart_upload_id"],
        part_number,
        data,
    )
    parts = _parts_map(row)
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
    row = _assert_session_access(pick(locked is None, lambda: None, lambda: dict(locked)), principal)
    parts = _parts_map(row)
    expected = math.ceil(int(row["total_size"]) / int(row["chunk_size"]))
    pick(
        len(parts) != expected,
        lambda: _raise(HTTPException(status_code=400, detail="Missing upload parts")),
        lambda: None,
    )

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
        "account_id": pick(row["account_id"] is not None, lambda: str(row["account_id"]), lambda: None),
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
