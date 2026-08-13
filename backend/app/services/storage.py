"""HTTP client to the storage service. Product and workers never talk to MinIO."""

from __future__ import annotations

import logging
import re
import uuid

import httpx
from fastapi import HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0)


def _client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        base_url=settings.storage_url.rstrip("/"),
        timeout=_TIMEOUT,
        headers={"X-Zivo-Internal-Key": settings.secret_key},
    )


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = "Storage unavailable"
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("detail"):
            detail = str(body["detail"])
    except Exception:
        if response.text:
            detail = response.text[:200]
    if response.status_code in {400, 401, 403, 404, 410, 413, 415}:
        raise HTTPException(status_code=response.status_code, detail=detail)
    logger.warning("storage service error status=%s detail=%s", response.status_code, detail)
    raise HTTPException(status_code=503, detail="Storage unavailable")


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        with _client() as client:
            response = client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        logger.warning("storage service unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="Storage unavailable") from exc
    _raise_for_status(response)
    return response


def slugify_filename(filename: str) -> str:
    base = filename.rsplit(".", 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return slug[:48] or "document"


def _safe_storage_filename(filename: str) -> str:
    """Sanitize a user-provided filename for inclusion in an object key."""
    base = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._-")
    return safe[:200] or "upload"


def ingest_tmp_key(document_id: uuid.UUID, stage: str) -> str:
    return f"tmp/ingest/{document_id}/{stage}.json"


def ensure_bucket() -> None:
    _request("POST", "/api/storage/internal/ensure-bucket")


def save_upload(account_id: uuid.UUID | None, filename: str, data: bytes, content_type: str) -> str:
    params: dict[str, str] = {
        "filename": filename,
        "content_type": content_type,
    }
    if account_id is not None:
        params["account_id"] = str(account_id)
    response = _request("PUT", "/api/storage/internal/objects", params=params, content=data)
    body = response.json()
    return str(body["storage_key"])


def put_bytes(storage_key: str, data: bytes, content_type: str, filename: str = "object") -> str:
    params = {
        "filename": filename,
        "content_type": content_type,
        "storage_key": storage_key,
    }
    response = _request("PUT", "/api/storage/internal/objects", params=params, content=data)
    body = response.json()
    return str(body["storage_key"])


def start_multipart_upload(storage_key: str, content_type: str) -> str:
    response = _request(
        "POST",
        "/api/storage/internal/multipart/start",
        params={"key": storage_key, "content_type": content_type},
    )
    return str(response.json()["upload_id"])


def upload_multipart_part(storage_key: str, upload_id: str, part_number: int, data: bytes) -> str:
    response = _request(
        "PUT",
        "/api/storage/internal/multipart/part",
        params={"key": storage_key, "upload_id": upload_id, "part_number": part_number},
        content=data,
    )
    return str(response.json()["etag"])


def complete_multipart_upload(storage_key: str, upload_id: str, parts: list[dict]) -> None:
    _request(
        "POST",
        "/api/storage/internal/multipart/complete",
        json={"key": storage_key, "upload_id": upload_id, "parts": parts},
    )


def abort_multipart_upload(storage_key: str, upload_id: str) -> None:
    _request(
        "POST",
        "/api/storage/internal/multipart/abort",
        params={"key": storage_key, "upload_id": upload_id},
    )


def fetch_object(storage_key: str) -> bytes:
    response = _request("GET", "/api/storage/internal/objects", params={"key": storage_key})
    return response.content


def put_json(storage_key: str, data: object) -> None:
    _request("PUT", "/api/storage/internal/objects/json", json={"key": storage_key, "data": data})


def get_json(storage_key: str) -> object:
    response = _request("GET", "/api/storage/internal/objects/json", params={"key": storage_key})
    return response.json()["data"]


def delete_object(storage_key: str) -> None:
    _request("DELETE", "/api/storage/internal/objects", params={"key": storage_key})


def object_exists(storage_key: str) -> bool:
    response = _request("GET", "/api/storage/internal/objects/exists", params={"key": storage_key})
    return bool(response.json().get("exists"))


def presigned_get_url(storage_key: str, expires: int = 3600) -> str:
    response = _request(
        "GET",
        "/api/storage/internal/objects/url",
        params={"key": storage_key, "expires": expires},
    )
    return str(response.json()["url"])


def get_object_meta(*, object_id: uuid.UUID | None = None, storage_key: str | None = None) -> dict:
    params: dict[str, str] = {}
    if object_id is not None:
        params["object_id"] = str(object_id)
    if storage_key:
        params["key"] = storage_key
    response = _request("GET", "/api/storage/internal/objects/meta", params=params)
    return response.json()
