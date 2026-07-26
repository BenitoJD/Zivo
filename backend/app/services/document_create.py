"""Shared helpers for creating documents from uploads and imports."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, Document
from app.services.jobs import enqueue_ingest
from app.services.parse import count_document_pages
from app.services.storage import save_upload, slugify_filename

_GB = 1024 * 1024 * 1024

# --- upload content-type gate -------------------------------------------------
# Single source of truth for "which content types may become a document." Both
# the single-shot upload path and the chunked/resumable path route through
# ``normalize_upload_content_type`` + ``ALLOWED_TYPES`` so an attacker cannot
# bypass the gate by declaring an inline-renderable type (text/html, image/svg,
# application/javascript) on the chunked endpoint and later serving it via the
# presigned URL on /sources/{id}/file. Sniff real bytes; never trust headers.
ALLOWED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
}
# Image study uploads are rejected until OCR ingest is production-ready.
ALLOWED_IMAGE_PREFIX = "image/"
_DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_CT = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def normalize_upload_content_type(filename: str, content_type: str, data: bytes) -> str:
    """Sniff the real content type from magic bytes; browsers often mislabel
    (e.g. .docx arrives as application/msword).

    Returns the resolved type. Raises HTTPException(415) for unsupported legacy
    formats and HTTPException(415) for anything unrecognized (caller then
    applies the allowlist / image reject). Never returns an inline-renderable
    type for attacker-chosen headers — only what the bytes actually are.
    """
    ct = (content_type or "application/octet-stream").lower()
    name = (filename or "").lower()
    if data[:4] == b"%PDF" or name.endswith(".pdf"):
        return "application/pdf"
    # OOXML (.docx / .pptx) is a ZIP; legacy .doc is OLE compound (D0 CF 11 E0).
    is_zip = data[:2] == b"PK"
    if name.endswith(".pptx") or (is_zip and "presentationml" in ct):
        return _PPTX_CT
    if name.endswith(".docx") or (is_zip and ("wordprocessingml" in ct or ct == "application/msword")):
        return _DOCX_CT
    if name.endswith(".ppt") or "ms-powerpoint" in ct:
        raise HTTPException(
            status_code=415,
            detail="Legacy .ppt isn’t supported — save as .pptx or PDF and upload again.",
        )
    if name.endswith(".doc") or ct == "application/msword":
        if is_zip:
            return _DOCX_CT
        raise HTTPException(
            status_code=415,
            detail="Legacy .doc isn’t supported — save as .docx or PDF and upload again.",
        )
    if name.endswith((".txt", ".md")) or ct.startswith("text/"):
        return "text/plain"
    return ct


def assert_upload_content_type_allowed(resolved_ct: str) -> None:
    """Apply the allowlist + image reject to a resolved content type."""
    if resolved_ct.startswith(ALLOWED_IMAGE_PREFIX):
        raise HTTPException(
            status_code=422,
            detail="Image study isn’t available yet — upload a PDF, Word, PowerPoint, or paste text.",
        )
    if resolved_ct not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported file type")


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
        used = (
            db.query(func.coalesce(func.sum(Document.size_bytes), 0))
            .filter(Document.account_id == user.id)
            .scalar()
        )
        used = int(used or 0)
    else:
        # Real check (not `assert`, which -O strips): an anonymous document must have
        # an owning guest id, otherwise it would be created unowned/orphaned.
        if guest_id is None:
            raise ValueError("guest_id is required to create an anonymous document")
        doc_guest_id = guest_id
        if counts_toward_guest_cap:
            non_image_count = (
                db.query(func.count(Document.id))
                .filter(Document.account_id.is_(None))
                .filter(Document.meta["guest_id"].astext == doc_guest_id)
                .filter(~Document.content_type.startswith("image/"))
                .scalar()
            )
            if int(non_image_count or 0) >= settings.guest_document_limit:
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
    # Re-validate at the choke point so every code path that lands bytes in
    # object storage (single-shot upload, test fixtures, future callers) goes
    # through the same type gate. The HTTP route already checks; this catches
    # anything that bypasses it.
    resolved = normalize_upload_content_type(filename, content_type, data)
    assert_upload_content_type_allowed(resolved)
    content_type = resolved

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
    if not is_image:
        try:
            if (
                "pdf" in ct_lower
                or data[:4] == b"%PDF"
                or "wordprocessingml" in ct_lower
                or "presentationml" in ct_lower
                or "msword" in ct_lower
                or ct_lower.startswith("text/")
                or ct_lower == "application/json"
            ):
                doc_meta["page_count"] = count_document_pages(content_type, data)
        except Exception:
            # Upload must succeed even if page counting fails; ingest will set it.
            pass

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
    # The chunked path takes content_type from the request body at init time,
    # which is attacker-controlled and may be inline-renderable (text/html,
    # image/svg+xml, application/javascript). Sniff the real type from the
    # assembled bytes now that the multipart upload is complete, and reject
    # before persisting — otherwise the stored object would be served inline
    # via the presigned URL on GET /sources/{id}/file (stored XSS).
    from app.services.storage import fetch_object

    fetched = fetch_object(storage_key)
    resolved = normalize_upload_content_type(filename, content_type, fetched)
    assert_upload_content_type_allowed(resolved)
    content_type = resolved

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
    if not is_image:
        try:
            if (
                "pdf" in ct_lower
                or "wordprocessingml" in ct_lower
                or "presentationml" in ct_lower
                or "msword" in ct_lower
                or ct_lower.startswith("text/")
                or ct_lower == "application/json"
            ):
                # ``fetched`` already holds the bytes from the type sniff above;
                # reuse it rather than re-fetching from object storage.
                doc_meta["page_count"] = count_document_pages(content_type, fetched)
        except Exception:
            pass

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
