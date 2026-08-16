"""Newspaper practice API — catalog and edition questions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account
from app.services import newspaper as newspaper_svc
from app.services.auth import get_optional_user
from app.services.document_learner_state import learner_key_for_user
from app.services.guest_session import guest_session_for_read
from app.services.session_design import (
    NEWSPAPER_EDITION_QUESTIONS_DEFAULT,
    plan_newspaper_edition_questions_limit,
)

router = APIRouter()


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)) -> dict:
    return newspaper_svc.list_catalog(db)


@router.get("/papers/{paper_slug}/days")
def paper_days(
    paper_slug: str,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    learner_key = learner_key_for_user(user.id if user else None, guest_id)
    return newspaper_svc.list_paper_days(db, paper_slug, learner_key=learner_key)


@router.get("/editions/{edition_id}")
def get_edition(edition_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    if not ed:
        raise HTTPException(status_code=404, detail="Edition not found")
    if not newspaper_repo.is_brand_allowed(db, ed["paper_slug"]):
        raise HTTPException(status_code=404, detail="Edition not found")
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
    limit: int = NEWSPAPER_EDITION_QUESTIONS_DEFAULT,
    db: Session = Depends(get_db),
) -> dict:
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    if ed and not newspaper_repo.is_brand_allowed(db, ed["paper_slug"]):
        raise HTTPException(status_code=404, detail="Edition not found")
    out = newspaper_svc.list_edition_questions(
        db, edition_id, limit=plan_newspaper_edition_questions_limit(limit)
    )
    if out["edition"] is None:
        raise HTTPException(status_code=404, detail="Edition not found")
    return out
