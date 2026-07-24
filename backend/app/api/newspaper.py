"""Newspaper practice API — catalog, questions, admin channel knob."""

from __future__ import annotations

import uuid

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


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)) -> dict:
    return newspaper_svc.list_catalog(db)


@router.get("/papers/{paper_slug}/days")
def paper_days(paper_slug: str, db: Session = Depends(get_db)) -> dict:
    return newspaper_svc.list_paper_days(db, paper_slug)


@router.get("/editions/{edition_id}")
def get_edition(edition_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    if not ed:
        raise HTTPException(status_code=404, detail="Edition not found")
    if not newspaper_repo.is_brand_allowed(db, ed["paper_slug"]):
        raise HTTPException(status_code=404, detail="Edition not found")
    # Practice catalog is last RETENTION_DAYS only — stale editions stay out.
    if not newspaper_svc.edition_in_practice_window(ed["edition_date"]):
        raise HTTPException(status_code=404, detail="Edition not found")
    return {
        "id": str(ed["id"]),
        "paper_slug": ed["paper_slug"],
        "paper_title": ed["paper_title"],
        "edition_date": ed["edition_date"].isoformat(),
        "status": ed["status"],
        "document_id": str(ed["document_id"]) if ed.get("document_id") else None,
    }


@router.get("/editions/{edition_id}/questions")
def edition_questions(
    edition_id: uuid.UUID,
    limit: int = 40,
    db: Session = Depends(get_db),
) -> dict:
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    if ed and not newspaper_repo.is_brand_allowed(db, ed["paper_slug"]):
        raise HTTPException(status_code=404, detail="Edition not found")
    out = newspaper_svc.list_edition_questions(db, edition_id, limit=min(limit, 80))
    if out["edition"] is None:
        raise HTTPException(status_code=404, detail="Edition not found")
    return out


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
