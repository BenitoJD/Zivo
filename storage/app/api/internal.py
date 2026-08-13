"""Cluster-internal object API. Product API and workers call these with the shared key."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import objects as object_service
from app.services.minio import (
    abort_multipart_upload,
    complete_multipart_upload,
    ensure_bucket,
    start_multipart_upload,
    upload_multipart_part,
)
from app.services.session import require_internal_key

router = APIRouter(
    prefix="/storage/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_key)],
)


class PutJsonIn(BaseModel):
    key: str = Field(min_length=1, max_length=1024)
    data: object


@router.post("/ensure-bucket")
def internal_ensure_bucket() -> dict[str, str]:
    ensure_bucket()
    return {"status": "ok"}


@router.put("/objects")
async def internal_put_object(
    request: Request,
    db: Session = Depends(get_db),
    filename: str = Query(min_length=1, max_length=512),
    content_type: str = Query(min_length=1, max_length=128),
    account_id: uuid.UUID | None = Query(default=None),
    guest_id: str | None = Query(default=None, max_length=32),
    storage_key: str | None = Query(default=None, max_length=1024),
) -> dict:
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Empty body")
    return object_service.put_bytes(
        db,
        data=data,
        filename=filename,
        content_type=content_type,
        account_id=account_id,
        guest_id=guest_id,
        storage_key=storage_key,
    )


@router.get("/objects")
def internal_get_object(
    key: str = Query(min_length=1, max_length=1024),
) -> Response:
    data = object_service.fetch_bytes(key)
    return Response(content=data, media_type="application/octet-stream")


@router.delete("/objects", status_code=204)
def internal_delete_object(
    db: Session = Depends(get_db),
    key: str = Query(min_length=1, max_length=1024),
) -> None:
    object_service.delete_bytes(db, key)


@router.get("/objects/url")
def internal_presign(
    key: str = Query(min_length=1, max_length=1024),
    expires: int = Query(default=3600, ge=60, le=86400),
) -> dict[str, str]:
    return {"url": object_service.presign(key, expires=expires)}


@router.get("/objects/meta")
def internal_meta(
    db: Session = Depends(get_db),
    object_id: uuid.UUID | None = Query(default=None),
    key: str | None = Query(default=None, max_length=1024),
) -> dict:
    return object_service.get_meta(db, object_id=object_id, storage_key=key)


@router.get("/objects/exists")
def internal_exists(key: str = Query(min_length=1, max_length=1024)) -> dict[str, bool]:
    return {"exists": object_service.exists(key)}


@router.put("/objects/json")
def internal_put_json(body: PutJsonIn) -> dict[str, str]:
    object_service.write_json(body.key, body.data)
    return {"status": "ok"}


@router.get("/objects/json")
def internal_get_json(key: str = Query(min_length=1, max_length=1024)) -> dict:
    return {"data": object_service.read_json(key)}


class MultipartCompleteIn(BaseModel):
    key: str = Field(min_length=1, max_length=1024)
    upload_id: str = Field(min_length=1, max_length=1024)
    parts: list[dict]


@router.post("/multipart/start")
def internal_multipart_start(
    key: str = Query(min_length=1, max_length=1024),
    content_type: str = Query(min_length=1, max_length=128),
) -> dict[str, str]:
    return {"upload_id": start_multipart_upload(key, content_type)}


@router.put("/multipart/part")
async def internal_multipart_part(
    request: Request,
    key: str = Query(min_length=1, max_length=1024),
    upload_id: str = Query(min_length=1, max_length=1024),
    part_number: int = Query(ge=1),
) -> dict[str, str]:
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Empty part body")
    etag = upload_multipart_part(key, upload_id, part_number, data)
    return {"etag": etag}


@router.post("/multipart/complete")
def internal_multipart_complete(body: MultipartCompleteIn) -> dict[str, str]:
    complete_multipart_upload(body.key, body.upload_id, body.parts)
    return {"status": "ok"}


@router.post("/multipart/abort")
def internal_multipart_abort(
    key: str = Query(min_length=1, max_length=1024),
    upload_id: str = Query(min_length=1, max_length=1024),
) -> dict[str, str]:
    abort_multipart_upload(key, upload_id)
    return {"status": "ok"}
