"""Artifact metadata, page selection, segments."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.models import Account
from app.repositories import workspace as workspace_repo
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.document_access import require_document, require_document_source
from app.services.guest_session import optional_guest_session, require_actor
from app.services.rate_limit import rate_limit_dependency
from app.services.background_prep import (
    PREP_MODE_BACKGROUND,
    PREP_MODE_NOW,
    apply_prep_meta,
    compute_prep_progress,
    is_background_prep,
    maybe_recover_stuck_background_prep,
)
from app.services.jobs import enqueue_full_range_ingest, enqueue_rag_window
from app.services.parse import refresh_document_page_count
from app.services.question_pool import reset_for_new_page_range
from app.services.rag_window import maybe_recover_stuck_indexing
from app.services.storage import presigned_get_url


router = APIRouter()

_LIST_SEGMENTS_LIMIT = 500


class PageRangeIn(BaseModel):
    from_page: int = Field(alias="from", ge=1)
    to_page: int = Field(alias="to", ge=1)
    pages: list[int] | None = None
    prep_mode: Literal["now", "background"] = PREP_MODE_NOW

    model_config = {"populate_by_name": True}


class ArtifactOut(BaseModel):
    id: uuid.UUID
    artifact_captured_at: datetime | None = None
    filename: str
    content_type: str
    status: str
    index_progress: int = 0
    meta: dict = {}
    ingest_kind: str | None = None


def _raise(exc: BaseException) -> None:
    raise exc


def _account_id(user: Account | None):
    return pick(bool(user), lambda: user.id, lambda: None)


@router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> ArtifactOut:
    doc = require_document(db, artifact_id, user, guest_id)
    refresh_document_page_count(db, doc)
    maybe_recover_stuck_indexing(db, doc)
    maybe_recover_stuck_background_prep(db, doc)
    db.refresh(doc)
    meta = dict(doc.meta or {})

    def _prep() -> None:
        progress = compute_prep_progress(db, doc)
        meta["prep_progress"] = progress
        doc.index_progress = int(progress["overall_pct"])

    pick(is_background_prep(doc), _prep, lambda: None)
    return ArtifactOut(
        id=doc.id,
        artifact_captured_at=doc.artifact_captured_at,
        filename=doc.filename,
        content_type=doc.content_type,
        status=doc.status,
        index_progress=doc.index_progress or 0,
        meta=doc.meta or {},
        ingest_kind=(doc.meta or {}).get("ingest_kind"),
    )


@router.get("/{artifact_id}/pages")
def get_pages(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    doc = require_document_source(db, artifact_id, user, guest_id)
    page_count = refresh_document_page_count(db, doc)
    return {
        "artifact_id": str(doc.id),
        "page_count": page_count,
        "presigned_url": presigned_get_url(doc.storage_key),
        "status": doc.status,
    }


@router.post(
    "/{artifact_id}/page-range",
    dependencies=[
        Depends(require_csrf_or_guest),
        Depends(rate_limit_dependency),
        Depends(require_actor),
    ],
)
def confirm_page_range(
    artifact_id: uuid.UUID,
    body: PageRangeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    doc = require_document_source(db, artifact_id, user, guest_id)
    page_count = refresh_document_page_count(db, doc) or body.to_page
    study_pages = sorted(set(filter(lambda p: p >= 1, body.pages or ())))

    def _explicit_selected() -> dict:
        return {"from": study_pages[0], "to": study_pages[-1], "pages": study_pages}

    def _range_selected() -> dict:
        return {"from": body.from_page, "to": body.to_page}

    selected = apply(
        first_match(
            (
                Rule(when=(Pred("explicit", "truthy"), Pred("empty", "truthy")), action="no_pages"),
                Rule(
                    when=(Pred("explicit", "truthy"), Pred("overflow", "truthy")),
                    action="overflow_pages",
                ),
                Rule(when=(Pred("explicit", "truthy"),), action="explicit_ok"),
                Rule(when=(Pred("inverted", "truthy"),), action="invalid_range"),
                Rule(when=(Pred("range_overflow", "truthy"),), action="overflow_range"),
                Rule(when=(), action="range_ok"),
            ),
            {
                "explicit": bool(body.pages),
                "empty": not study_pages,
                "overflow": any(p > page_count for p in study_pages),
                "inverted": body.to_page < body.from_page,
                "range_overflow": body.to_page > page_count,
            },
        ).action,
        {
            "no_pages": lambda: _raise(HTTPException(status_code=400, detail="No pages selected")),
            "overflow_pages": lambda: _raise(
                HTTPException(status_code=400, detail="Page selection exceeds document")
            ),
            "explicit_ok": _explicit_selected,
            "invalid_range": lambda: _raise(
                HTTPException(status_code=400, detail="Invalid page range")
            ),
            "overflow_range": lambda: _raise(
                HTTPException(status_code=400, detail="Page range exceeds document")
            ),
            "range_ok": _range_selected,
        },
    )
    reset_for_new_page_range(db, doc, selected)
    from app.services.background_prep import is_background_prep

    already_cooked = (
        doc.status == "ready"
        and bool((doc.meta or {}).get("prep_complete"))
        and not is_background_prep(doc)
    )

    def _restore_cooked() -> None:
        meta = dict(doc.meta or {})
        meta["prep_complete"] = True
        meta["prep_phase"] = "complete"
        meta["prep_mode"] = "background"
        doc.meta = meta
        from sqlalchemy.orm.attributes import flag_modified as _flag

        _flag(doc, "meta")
        doc.index_progress = 100

    pick(already_cooked, _restore_cooked, lambda: apply_prep_meta(db, doc, prep_mode=body.prep_mode))

    def _drop_budget() -> None:
        meta = dict(doc.meta or {})
        meta.pop("preferred_question_budget", None)
        doc.meta = meta
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(doc, "meta")

    pick("preferred_question_budget" in (doc.meta or {}), _drop_budget, lambda: None)
    study_pages = selected.get("pages") or list(
        range(int(selected["from"]), int(selected["to"]) + 1)
    )
    page_from = pick(
        bool(study_pages),
        lambda: int(study_pages[0]),
        lambda: int(selected["from"]),
    )
    captured = doc.artifact_captured_at or doc.created_at

    def _keep_ready() -> dict:
        workspace_repo.upsert_workspace(
            db,
            account_id=_account_id(user),
            artifact_id=doc.id,
            artifact_captured_at=captured,
            status="ready",
            page_count=page_count,
            selected_range=selected,
        )
        doc.status = "ready"
        db.commit()
        return {"document_id": str(doc.id), "page_from": page_from, "status": "ready"}

    def _enqueue() -> dict:
        workspace_status = choose(
            body.prep_mode == PREP_MODE_BACKGROUND, "prepping", "indexing"
        )
        workspace_repo.upsert_workspace(
            db,
            account_id=_account_id(user),
            artifact_id=doc.id,
            artifact_captured_at=captured,
            status=workspace_status,
            page_count=page_count,
            selected_range=selected,
        )

        def _background():
            return enqueue_full_range_ingest(
                db,
                document_id=doc.id,
                account_id=_account_id(user),
                current_page=page_from,
            )

        def _foreground():
            doc.status = "indexing"
            return enqueue_rag_window(
                db,
                document_id=doc.id,
                account_id=_account_id(user),
                current_page=page_from,
            )

        job = pick(body.prep_mode == PREP_MODE_BACKGROUND, _background, _foreground)
        db.commit()
        return {
            "activity_id": str(job.payload.get("activity_id", job.id)),
            "job_id": str(job.id),
            "prep_mode": body.prep_mode,
        }

    return pick(already_cooked, _keep_ready, _enqueue)


@router.get("/{artifact_id}/segments")
def list_segments(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> list[dict]:
    doc = require_document_source(db, artifact_id, user, guest_id)
    rows = db.execute(
        text(
            """
            SELECT id, page_start, page_end, text, meta
            FROM qb.document_chunks
            WHERE document_id = :doc_id
            ORDER BY page_start, id
            LIMIT :limit
            """
        ),
        {"doc_id": doc.id, "limit": _LIST_SEGMENTS_LIMIT},
    ).mappings().all()
    return [dict(r) for r in rows]
