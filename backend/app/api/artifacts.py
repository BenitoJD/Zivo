"""Artifact metadata, page selection, segments."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account
from app.repositories import workspace as workspace_repo
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.api.access import require_document
from app.services.guest_session import optional_guest_session
from app.services.jobs import enqueue_rag_window
from app.services.question_pool import reset_for_new_page_range
from app.services.storage import presigned_get_url

router = APIRouter()

_LIST_SEGMENTS_LIMIT = 500


class PageRangeIn(BaseModel):
    from_page: int = Field(alias="from", ge=1)
    to_page: int = Field(alias="to", ge=1)
    pages: list[int] | None = None

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
    doc = require_document(db, artifact_id, user, guest_id)
    page_count = (doc.meta or {}).get("page_count") or 1
    return {
        "artifact_id": str(doc.id),
        "page_count": page_count,
        "presigned_url": presigned_get_url(doc.storage_key),
        "status": doc.status,
    }


@router.post("/{artifact_id}/page-range", dependencies=[Depends(require_csrf_or_guest)])
def confirm_page_range(
    artifact_id: uuid.UUID,
    body: PageRangeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> dict:
    doc = require_document(db, artifact_id, user, guest_id)
    page_count = (doc.meta or {}).get("page_count") or body.to_page

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
    doc.status = "indexing"
    captured = doc.artifact_captured_at or doc.created_at
    workspace_repo.upsert_workspace(
        db,
        account_id=user.id if user else None,
        artifact_id=doc.id,
        artifact_captured_at=captured,
        status="indexing",
        page_count=page_count,
        selected_range=selected,
    )
    job = enqueue_rag_window(
        db,
        document_id=doc.id,
        account_id=user.id if user else None,
        current_page=page_from,
    )
    db.commit()
    return {"activity_id": str(job.payload.get("activity_id", job.id)), "job_id": str(job.id)}


@router.get("/{artifact_id}/segments")
def list_segments(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> list[dict]:
    doc = require_document(db, artifact_id, user, guest_id)
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
