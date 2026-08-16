"""Newspaper practice API — catalog and edition questions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.engine_runtime import Pred, Rule, apply, first_match, pick
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


def _raise_http(status: int, detail: str) -> None:
    raise HTTPException(status_code=status, detail=detail)


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
    account_id = pick(user is not None, lambda: user.id, lambda: None)
    learner_key = learner_key_for_user(account_id, guest_id)
    return newspaper_svc.list_paper_days(db, paper_slug, learner_key=learner_key)


@router.get("/editions/{edition_id}")
def get_edition(edition_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    apply(
        first_match(
            (
                Rule(when=(Pred("has_ed", "falsey"),), action="missing"),
                Rule(when=(Pred("allowed", "falsey"),), action="missing"),
                Rule(when=(Pred("in_window", "falsey"),), action="missing"),
                Rule(when=(), action="ok"),
            ),
            {
                "has_ed": bool(ed),
                "allowed": bool(ed) and newspaper_repo.is_brand_allowed(db, ed["paper_slug"]),
                "in_window": bool(ed)
                and newspaper_svc.edition_in_practice_window(ed["edition_date"]),
            },
        ).action,
        {
            "missing": lambda: _raise_http(404, "Edition not found"),
            "ok": lambda: None,
        },
    )
    return {
        "id": str(ed["id"]),
        "paper_slug": ed["paper_slug"],
        "paper_title": ed["paper_title"],
        "edition_date": ed["edition_date"].isoformat(),
        "status": ed["status"],
        "document_id": pick(
            bool(ed.get("document_id")),
            lambda: str(ed["document_id"]),
            lambda: None,
        ),
    }


@router.get("/editions/{edition_id}/questions")
def edition_questions(
    edition_id: uuid.UUID,
    limit: int = NEWSPAPER_EDITION_QUESTIONS_DEFAULT,
    db: Session = Depends(get_db),
) -> dict:
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    pick(
        bool(ed) and not newspaper_repo.is_brand_allowed(db, ed["paper_slug"]),
        lambda: _raise_http(404, "Edition not found"),
        lambda: None,
    )
    out = newspaper_svc.list_edition_questions(
        db, edition_id, limit=plan_newspaper_edition_questions_limit(limit)
    )
    pick(
        out["edition"] is None,
        lambda: _raise_http(404, "Edition not found"),
        lambda: None,
    )
    return out
