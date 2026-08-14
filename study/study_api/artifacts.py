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



@router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> ArtifactOut:
    doc = require_document(db, artifact_id, user, guest_id)
    # Heal stale page_count / bogus single-page selection before the FE decides
    # whether to show the page picker.
    refresh_document_page_count(db, doc)
    maybe_recover_stuck_indexing(db, doc)
    maybe_recover_stuck_background_prep(db, doc)
    db.refresh(doc)
    meta = dict(doc.meta or {})
    if is_background_prep(doc):
        progress = compute_prep_progress(db, doc)
        meta["prep_progress"] = progress
        doc.index_progress = int(progress["overall_pct"])
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
    # Learners never get the newspaper PDF — admins may for ops.
    doc = require_document_source(db, artifact_id, user, guest_id)
    # Heal DOCX/paste that landed as page_count=1 before soft pagination.
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
    # Re-count from bytes so a healed multi-page DOCX can accept a real range.
    page_count = refresh_document_page_count(db, doc) or body.to_page

    if body.pages:
        study_pages = sorted({p for p in body.pages if p >= 1})
        if not study_pages:
            raise HTTPException(status_code=400, detail="No pages selected")
        if any(p > page_count for p in study_pages):
            raise HTTPException(status_code=400, detail="Page selection exceeds document")
        selected = {"from": study_pages[0], "to": study_pages[-1], "pages": study_pages}
    else:
        if body.to_page < body.from_page:
            raise HTTPException(status_code=400, detail="Invalid page range")
        if body.to_page > page_count:
            raise HTTPException(status_code=400, detail="Page range exceeds document")
        selected = {"from": body.from_page, "to": body.to_page}
    reset_for_new_page_range(db, doc, selected)
    # A doc that was already background-prepped to ready (partial pool) must not
    # be reset to a fresh foreground index when the learner confirms pages —
    # reset_for_new_page_range wipes prep_complete/pool state, and the 496-page
    # seduction PDF got re-indexed from zero after promotion. Keep the cooked
    # state; only the page-range selection changes.
    from app.services.background_prep import is_background_prep

    already_cooked = (
        doc.status == "ready"
        and bool((doc.meta or {}).get("prep_complete"))
        and not is_background_prep(doc)
    )
    if already_cooked:
        # Restore the cooked flags that reset_for_new_page_range cleared.
        meta = dict(doc.meta or {})
        meta["prep_complete"] = True
        meta["prep_phase"] = "complete"
        meta["prep_mode"] = "background"
        doc.meta = meta
        from sqlalchemy.orm.attributes import flag_modified as _flag

        _flag(doc, "meta")
        doc.index_progress = 100
    else:
        apply_prep_meta(db, doc, prep_mode=body.prep_mode)
    # Drop legacy Settings override so triage/heuristic owns the page budget.
    if "preferred_question_budget" in (doc.meta or {}):
        meta = dict(doc.meta or {})
        meta.pop("preferred_question_budget", None)
        doc.meta = meta
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(doc, "meta")
    study_pages = selected.get("pages") or list(
        range(int(selected["from"]), int(selected["to"]) + 1)
    )
    page_from = int(study_pages[0]) if study_pages else int(selected["from"])
    captured = doc.artifact_captured_at or doc.created_at
    # A cooked doc (ready partial pool) keeps its status — do not re-enqueue a
    # fresh index/ingest, which would re-run the whole pipeline from zero.
    if already_cooked:
        workspace_repo.upsert_workspace(
            db,
            account_id=user.id if user else None,
            artifact_id=doc.id,
            artifact_captured_at=captured,
            status="ready",
            page_count=page_count,
            selected_range=selected,
        )
        doc.status = "ready"
        db.commit()
        return {"document_id": str(doc.id), "page_from": page_from, "status": "ready"}

    workspace_status = "prepping" if body.prep_mode == PREP_MODE_BACKGROUND else "indexing"
    workspace_repo.upsert_workspace(
        db,
        account_id=user.id if user else None,
        artifact_id=doc.id,
        artifact_captured_at=captured,
        status=workspace_status,
        page_count=page_count,
        selected_range=selected,
    )
    if body.prep_mode == PREP_MODE_BACKGROUND:
        job = enqueue_full_range_ingest(
            db,
            document_id=doc.id,
            account_id=user.id if user else None,
            current_page=page_from,
        )
    else:
        doc.status = "indexing"
        job = enqueue_rag_window(
            db,
            document_id=doc.id,
            account_id=user.id if user else None,
            current_page=page_from,
        )
    db.commit()
    return {
        "activity_id": str(job.payload.get("activity_id", job.id)),
        "job_id": str(job.id),
        "prep_mode": body.prep_mode,
    }


@router.get("/{artifact_id}/segments")
def list_segments(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> list[dict]:
    # Full chunk text dump — same newspaper source gate as /pages and /file.
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
