"""Newspaper ingest admin HTTP — channel, allowlist, brands.

Practice catalog routes live on the practice service. Telethon ingest stays
on the existing worker chart.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import newspaper as newspaper_svc
from app.services.auth import require_admin, require_csrf

router = APIRouter()


class ChannelIn(BaseModel):
    channel_ref: str = Field(min_length=1, max_length=512)
    channel_label: str = Field(default="", max_length=256)


class AllowlistIn(BaseModel):
    allowlist_only: bool


class BrandEnabledIn(BaseModel):
    enabled: bool


@router.get("/admin/channel", dependencies=[Depends(require_admin)])
def get_channel(db: Session = Depends(get_db)) -> dict:
    return newspaper_svc.get_channel(db)


@router.patch("/admin/channel", dependencies=[Depends(require_admin), Depends(require_csrf)])
def put_channel(body: ChannelIn, db: Session = Depends(get_db)) -> dict:
    try:
        return newspaper_svc.update_channel(
            db, channel_ref=body.channel_ref, channel_label=body.channel_label
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/admin/brands", dependencies=[Depends(require_admin)])
def get_brands(db: Session = Depends(get_db)) -> dict:
    return newspaper_svc.list_admin_brands(db)


@router.patch("/admin/allowlist", dependencies=[Depends(require_admin), Depends(require_csrf)])
def patch_allowlist(body: AllowlistIn, db: Session = Depends(get_db)) -> dict:
    return newspaper_svc.set_allowlist_only(db, allowlist_only=body.allowlist_only)


@router.patch(
    "/admin/brands/{paper_slug}",
    dependencies=[Depends(require_admin), Depends(require_csrf)],
)
def patch_brand(paper_slug: str, body: BrandEnabledIn, db: Session = Depends(get_db)) -> dict:
    try:
        return newspaper_svc.set_brand_enabled(
            db, paper_slug=paper_slug, enabled=body.enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
