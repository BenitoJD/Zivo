"""Chunked / resumable document uploads."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.documents import DocumentOut
from app.db import SessionLocal, get_db
from app.models import Account
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.chunked_upload import (
    abort_upload_session,
    complete_upload_session,
    init_upload_session,
    upload_part,
    upload_session_status,
)
from app.services.guest_session import optional_guest_session
from app.services.rate_limit import rate_limit_dependency

router = APIRouter()


class ChunkedInitIn(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=3, max_length=128)
    total_size: int = Field(gt=0)
    chunk_size: int | None = Field(default=None, gt=0, le=32 * 1024 * 1024)


@router.post("/init", dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def chunked_init(
    body: ChunkedInitIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    db.close()

    def _run() -> dict:
        with SessionLocal() as session:
            return init_upload_session(
                session,
                user=user,
                guest_id=guest_id,
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
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    db.close()

    def _run() -> dict:
        with SessionLocal() as session:
            return upload_session_status(session, session_id, user=user, guest_id=guest_id)

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
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Empty part body")
    db.close()

    def _run() -> dict:
        with SessionLocal() as session:
            return upload_part(
                session,
                session_id,
                part_number,
                data,
                user=user,
                guest_id=guest_id,
            )

    return await asyncio.to_thread(_run)


@router.post(
    "/{session_id}/complete",
    response_model=DocumentOut,
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def chunked_complete(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> DocumentOut:
    db.close()

    def _run() -> DocumentOut:
        with SessionLocal() as session:
            return complete_upload_session(session, session_id, user=user, guest_id=guest_id)

    return await asyncio.to_thread(_run)


@router.delete(
    "/{session_id}",
    status_code=204,
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def chunked_abort(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> None:
    abort_upload_session(db, session_id, user=user, guest_id=guest_id)
