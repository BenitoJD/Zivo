"""Shared helpers for creating documents from uploads and imports."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, Document
from app.services.jobs import enqueue_ingest
from app.services.parse import count_pdf_pages
from app.services.storage import save_upload, slugify_filename

_GB = 1024 * 1024 * 1024


def guest_storage_used(db: Session, guest_id: str) -> int:
    rows = (
        db.query(Document.size_bytes)
        .filter(Document.account_id.is_(None))
        .filter(Document.meta["guest_id"].astext == guest_id)
        .all()
    )
    return sum(row[0] for row in rows)


def assert_storage_available(
    db: Session,
    *,
    user: Account | None,
    guest_id: str | None,
    size_bytes: int,
    counts_toward_guest_cap: bool,
) -> str | None:
    settings = get_settings()
    doc_guest_id: str | None = None

    if user:
        # Row-lock the user's existing documents so concurrent uploads can't
        # both pass the quota check before either commits.
        rows = (
            db.query(Document.size_bytes)
            .filter(Document.account_id == user.id)
            .with_for_update()
            .all()
        )
        used = sum(row[0] for row in rows)
    else:
        assert guest_id is not None
        doc_guest_id = guest_id
        if counts_toward_guest_cap:
            # Same race protection for guests: lock their existing rows
            # before counting toward the per-guest cap.
            existing = (
                db.query(Document)
                .filter(Document.account_id.is_(None))
                .filter(Document.meta["guest_id"].astext == doc_guest_id)
                .with_for_update()
                .all()
            )
            non_image = [d for d in existing if not d.content_type.startswith("image/")]
            if len(non_image) >= settings.guest_document_limit:
                raise HTTPException(status_code=409, detail="Sign in to add more documents")
        used = guest_storage_used(db, doc_guest_id)

    if used + size_bytes > settings.storage_limit_bytes:
        raise HTTPException(status_code=413, detail="Storage quota exceeded")

    return doc_guest_id


def create_document_record(
    db: Session,
    *,
    user: Account | None,
    guest_id: str | None,
    filename: str,
    content_type: str,
    data: bytes,
    meta: dict | None = None,
) -> Document:
    settings = get_settings()
    if len(data) > settings.max_upload_bytes:
        limit_gb = settings.max_upload_bytes // _GB
        raise HTTPException(status_code=413, detail=f"File too large (max {limit_gb}GB)")

    is_image = content_type.startswith("image/")
    doc_guest_id = assert_storage_available(
        db,
        user=user,
        guest_id=guest_id,
        size_bytes=len(data),
        counts_toward_guest_cap=not is_image,
    )

    slug = slugify_filename(filename)
    storage_key = save_upload(user.id if user else None, filename, data, content_type)

    doc_meta: dict = dict(meta or {})
    if doc_guest_id:
        doc_meta["guest_id"] = doc_guest_id

    ct_lower = (content_type or "").lower()
    if "pdf" in ct_lower or data[:4] == b"%PDF":
        doc_meta["page_count"] = count_pdf_pages(data)

    is_image = content_type.startswith("image/")

    doc = Document(
        account_id=user.id if user else None,
        slug=slug,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        storage_key=storage_key,
        status="indexing" if is_image else "pending",
        meta=doc_meta,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    if is_image:
        enqueue_ingest(db, doc.id)
    return doc


def create_document_from_storage(
    db: Session,
    *,
    user: Account | None,
    guest_id: str | None,
    filename: str,
    content_type: str,
    storage_key: str,
    size_bytes: int,
    meta: dict | None = None,
    guest_id_override: str | None = None,
) -> Document:
    settings = get_settings()
    if size_bytes > settings.max_upload_bytes:
        limit_gb = settings.max_upload_bytes // _GB
        raise HTTPException(status_code=413, detail=f"File too large (max {limit_gb}GB)")

    is_image = content_type.startswith("image/")
    doc_guest_id = guest_id_override
    if doc_guest_id is None:
        doc_guest_id = assert_storage_available(
            db,
            user=user,
            guest_id=guest_id,
            size_bytes=size_bytes,
            counts_toward_guest_cap=not is_image,
        )

    slug = slugify_filename(filename)
    doc_meta: dict = dict(meta or {})
    if doc_guest_id:
        doc_meta["guest_id"] = doc_guest_id

    ct_lower = (content_type or "").lower()
    if "pdf" in ct_lower:
        from app.services.storage import fetch_object

        data = fetch_object(storage_key)
        if data[:4] == b"%PDF":
            doc_meta["page_count"] = count_pdf_pages(data)

    doc = Document(
        account_id=user.id if user else None,
        slug=slug,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        status="indexing" if is_image else "pending",
        meta=doc_meta,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    if is_image:
        enqueue_ingest(db, doc.id)
    return doc
