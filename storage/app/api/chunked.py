"""Browser chunked-upload HTTP. Path prefix /api/storage/chunked."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.engine_runtime import pick
from app.services.chunked import (
    abort_upload_session,
    complete_upload_session,
    init_upload_session,
    upload_part,
    upload_session_status,
)
from app.services.guest import Principal
from app.services.rate_limit import rate_limit_dependency
from app.services.session import optional_principal, require_csrf_or_guest


def _raise(exc: BaseException) -> None:
    raise exc


router = APIRouter(prefix="/storage/chunked", tags=["chunked-uploads"])


class ChunkedInitIn(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=3, max_length=128)
    total_size: int = Field(gt=0)
    chunk_size: int | None = Field(default=None, gt=0, le=32 * 1024 * 1024)


@router.post("/init", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def chunked_init(
    body: ChunkedInitIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(optional_principal),
) -> dict:
    db.close()

    def _run() -> dict:
        with SessionLocal() as session:
            return init_upload_session(
                session,
                principal=principal,
                filename=body.filename,
                content_type=body.content_type.lower(),
                total_size=body.total_size,
                chunk_size=body.chunk_size,
            )

    return await asyncio.to_thread(_run)


@router.get("/{session_id}", dependencies=[Depends(rate_limit_dependency)])
async def chunked_status(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(optional_principal),
) -> dict:
    db.close()

    def _run() -> dict:
        with SessionLocal() as session:
            return upload_session_status(session, session_id, principal=principal)

    return await asyncio.to_thread(_run)


@router.put(
    "/{session_id}/parts/{part_number}",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def chunked_part(
    session_id: uuid.UUID,
    part_number: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(optional_principal),
) -> dict:
    data = await request.body()
    pick(not data, lambda: _raise(HTTPException(status_code=400, detail="Empty part body")), lambda: None)
    db.close()

    def _run() -> dict:
        with SessionLocal() as session:
            return upload_part(
                session,
                session_id,
                part_number,
                data,
                principal=principal,
            )

    return await asyncio.to_thread(_run)


@router.post(
    "/{session_id}/complete",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def chunked_complete(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(optional_principal),
) -> dict:
    db.close()

    def _run() -> dict:
        with SessionLocal() as session:
            return complete_upload_session(session, session_id, principal=principal)

    return await asyncio.to_thread(_run)


@router.delete(
    "/{session_id}",
    status_code=204,
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def chunked_abort(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(optional_principal),
) -> None:
    abort_upload_session(db, session_id, principal=principal)
