"""Object metadata + byte operations owned by the storage service."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engine_runtime import pick
from app.services.minio import (
    build_storage_key,
    delete_object as minio_delete,
    fetch_object as minio_fetch,
    get_json as minio_get_json,
    object_exists as minio_exists,
    presigned_get_url as minio_presign,
    put_json as minio_put_json,
    put_object,
)


def _raise(exc: BaseException) -> None:
    raise exc


def _raise_from(cause: BaseException, wrapped: BaseException) -> None:
    raise wrapped from cause


def record_object(
    db: Session,
    *,
    storage_key: str,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    filename: str,
    content_type: str,
    size_bytes: int,
) -> dict[str, Any]:
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
            "storage_key": storage_key,
            "account_id": account_id,
            "guest_id": guest_id,
            "filename": filename[:512],
            "content_type": content_type[:128],
            "size_bytes": size_bytes,
        },
    )
    db.commit()
    return {
        "object_id": str(object_id),
        "storage_key": storage_key,
        "filename": filename[:512],
        "content_type": content_type[:128],
        "size_bytes": size_bytes,
        "account_id": pick(account_id is not None, lambda: str(account_id), lambda: None),
        "guest_id": guest_id,
    }


def put_bytes(
    db: Session,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    storage_key: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    pick(
        len(data) < 1 or len(data) > settings.max_upload_bytes,
        lambda: _raise(
            HTTPException(
                status_code=413,
                detail=f"File too large (max {settings.max_upload_bytes // (1024 * 1024 * 1024)}GB)",
            )
        ),
        lambda: None,
    )
    key = storage_key or build_storage_key(account_id, filename)
    put_object(key, data, content_type)
    return record_object(
        db,
        storage_key=key,
        account_id=account_id,
        guest_id=guest_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
    )


def get_meta(db: Session, *, object_id: uuid.UUID | None = None, storage_key: str | None = None) -> dict[str, Any]:
    pick(
        object_id is None and not storage_key,
        lambda: _raise(HTTPException(status_code=400, detail="object_id or key required")),
        lambda: None,
    )

    def _by_id():
        return db.execute(
            text(
                """
                SELECT id, storage_key, account_id, guest_id, filename, content_type, size_bytes
                FROM storage.object
                WHERE id = :id
                """
            ),
            {"id": object_id},
        ).mappings().first()

    def _by_key():
        return db.execute(
            text(
                """
                SELECT id, storage_key, account_id, guest_id, filename, content_type, size_bytes
                FROM storage.object
                WHERE storage_key = :key
                """
            ),
            {"key": storage_key},
        ).mappings().first()

    row = pick(object_id is not None, _by_id, _by_key)
    pick(not row, lambda: _raise(HTTPException(status_code=404, detail="Object not found")), lambda: None)
    return {
        "object_id": str(row["id"]),
        "storage_key": row["storage_key"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "size_bytes": int(row["size_bytes"]),
        "account_id": pick(row["account_id"] is not None, lambda: str(row["account_id"]), lambda: None),
        "guest_id": row["guest_id"],
    }


def fetch_bytes(storage_key: str) -> bytes:
    try:
        return minio_fetch(storage_key)
    except Exception as exc:
        _raise_from(exc, HTTPException(status_code=404, detail="Object not found"))


def delete_bytes(db: Session, storage_key: str) -> None:
    minio_delete(storage_key)
    db.execute(text("DELETE FROM storage.object WHERE storage_key = :key"), {"key": storage_key})
    db.commit()


def presign(storage_key: str, expires: int = 3600) -> str:
    return minio_presign(storage_key, expires=expires)


def exists(storage_key: str) -> bool:
    return minio_exists(storage_key)


def write_json(storage_key: str, data: object) -> None:
    minio_put_json(storage_key, data)


def read_json(storage_key: str) -> object:
    try:
        return minio_get_json(storage_key)
    except Exception as exc:
        _raise_from(exc, HTTPException(status_code=404, detail="Object not found"))
