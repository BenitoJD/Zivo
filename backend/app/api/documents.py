import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.access import require_document
from app.db import get_db
from app.models import Account, Document, DocumentChunk, User
from app.services.auth import get_current_user, get_optional_user, require_csrf, require_csrf_or_guest
from app.services.document_create import create_document_record
from app.services.document_purge import purge_document, purge_ingest_tmp
from app.services.guest import document_owned_by_guest
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.jobs import enqueue_summarize
from app.services.storage import delete_object, presigned_get_url
from app.services.web_import import (
    WebImportError,
    article_filename,
    article_from_pasted_text,
    build_document_meta,
    encode_article_pages,
    fetch_and_extract,
    paginate_reader_text,
)
from app.services.youtube import fetch_youtube_transcript, is_youtube_url

from app.config import get_settings
from app.services.rate_limit import rate_limit_dependency

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

_READ_CHUNK_BYTES = 1024 * 1024
_LIST_DOCUMENTS_LIMIT = 100
_CHUNK_PAGE_LIMIT = 500

DEMO_DOC_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
ALLOWED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "image/png",
    "image/jpeg",
    "image/webp",
}
# Image uploads fall back to this allowlist (excludes SVG, which can carry
# script payloads that get rendered by viewers downstream).
ALLOWED_IMAGE_PREFIX = "image/"


class DocumentOut(BaseModel):
    id: uuid.UUID
    slug: str
    filename: str
    content_type: str
    size_bytes: int
    status: str
    index_progress: int
    meta: dict = {}

    model_config = {"from_attributes": True}


class SummarizeOut(BaseModel):
    job_id: uuid.UUID
    status: str


class ImportUrlIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)


class ImportTextIn(BaseModel):
    text: str = Field(min_length=40, max_length=500_000)
    title: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)


class ImportGithubIn(BaseModel):
    github_url: str = Field(min_length=8, max_length=2048)


@router.get("/demo", response_model=DocumentOut)
def get_demo_document(db: Session = Depends(get_db)) -> Document:
    doc = db.get(Document, DEMO_DOC_ID)
    if not doc:
        raise HTTPException(status_code=404, detail="Demo document not seeded")
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> list[Document]:
    if user:
        return (
            db.query(Document)
            .filter(Document.account_id == user.id)
            .order_by(Document.created_at.desc())
            .limit(_LIST_DOCUMENTS_LIMIT)
            .all()
        )

    # No user → guest scope. A real check (not `assert`, which -O strips): without a
    # guest id there are no guest documents — and we must never fall through to an
    # unfiltered query that could surface another anonymous user's documents.
    if guest_id is None:
        return []
    return (
        db.query(Document)
        .filter(Document.account_id.is_(None))
        .filter(Document.meta["guest_id"].astext == guest_id)
        .order_by(Document.created_at.desc())
        .limit(_LIST_DOCUMENTS_LIMIT)
        .all()
    )


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> Document:
    return require_document(db, document_id, user, guest_id)


@router.get("/{document_id}/file")
def get_document_file(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    doc = require_document(db, document_id, user, guest_id)
    if doc.meta and doc.meta.get("is_demo"):
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.page_start.asc())
            .all()
        )
        text = "\n\n".join(c.text for c in chunks if c.text)
        return Response(content=text.encode("utf-8"), media_type="text/plain")
    url = presigned_get_url(doc.storage_key)
    return RedirectResponse(url)


@router.get("/{document_id}/pages")
def get_document_pages(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    require_document(db, document_id, user, guest_id)
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.page_start.asc())
        .limit(_CHUNK_PAGE_LIMIT)
        .all()
    )
    pages: dict[int, str] = {}
    for c in chunks:
        existing = pages.get(c.page_start)
        pages[c.page_start] = f"{existing}\n\n{c.text}" if existing else c.text
    return {"pages": [{"page": p, "text": t} for p, t in sorted(pages.items())]}


async def _read_upload_capped(request: Request, file: UploadFile) -> bytes:
    max_bytes = settings.max_upload_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=413, detail="File too large") from exc
        if declared > max_bytes:
            raise HTTPException(status_code=413, detail="File too large")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="File too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=DocumentOut, dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> Document:
    data = await _read_upload_capped(request, file)

    ct = (file.content_type or "application/octet-stream").lower()
    if ct not in ALLOWED_TYPES:
        # Allow common raster image types only; reject SVG (script vector).
        if not (ct.startswith(ALLOWED_IMAGE_PREFIX) and ct not in {"image/svg+xml", "image/svg"}):
            raise HTTPException(status_code=415, detail="Unsupported file type")

    return await asyncio.to_thread(
        create_document_record,
        db,
        user=user,
        guest_id=guest_id,
        filename=file.filename or "document",
        content_type=ct,
        data=data,
    )


@router.post("/import-url", response_model=DocumentOut, dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def import_document_from_url(
    body: ImportUrlIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> Document:
    is_youtube = is_youtube_url(body.url)
    try:
        if is_youtube:
            # Scribely-style: paste a YouTube link → study its transcript.
            article = await asyncio.to_thread(fetch_youtube_transcript, body.url)
        else:
            article = await fetch_and_extract(body.url)
    except WebImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pages = paginate_reader_text(article.text)
    data = encode_article_pages(pages)
    meta = build_document_meta(article, source_type="youtube" if is_youtube else "url")
    meta["page_count"] = len(pages)
    filename = article_filename(article.title, article.source_domain)
    return await asyncio.to_thread(
        create_document_record,
        db,
        user=user,
        guest_id=guest_id,
        filename=filename,
        content_type="application/json",
        data=data,
        meta=meta,
    )


@router.post("/import-text", response_model=DocumentOut, dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def import_document_from_text(
    body: ImportTextIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> Document:
    try:
        article = article_from_pasted_text(body.text, title=body.title, source_url=body.source_url)
    except WebImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pages = paginate_reader_text(article.text)
    data = encode_article_pages(pages)
    meta = build_document_meta(article, source_type="paste" if not article.source_url else "url")
    meta["page_count"] = len(pages)
    filename = article_filename(article.title, article.source_domain)
    return await asyncio.to_thread(
        create_document_record,
        db,
        user=user,
        guest_id=guest_id,
        filename=filename,
        content_type="application/json",
        data=data,
        meta=meta,
    )


@router.post("/import-github", response_model=DocumentOut, dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def import_document_from_github(
    body: ImportGithubIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> Document:
    url = body.github_url.strip()
    if "github.com" not in url:
        raise HTTPException(status_code=422, detail="Not a GitHub URL")
    try:
        article = await fetch_and_extract(url)
    except WebImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pages = paginate_reader_text(article.text)
    data = encode_article_pages(pages)
    meta = build_document_meta(article, source_type="github")
    meta["page_count"] = len(pages)
    filename = article_filename(article.title, article.source_domain)
    return await asyncio.to_thread(
        create_document_record,
        db,
        user=user,
        guest_id=guest_id,
        filename=filename,
        content_type="application/json",
        data=data,
        meta=meta,
    )


@router.post("/{document_id}/summarize", response_model=SummarizeOut, dependencies=[Depends(require_csrf)])
def summarize_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    doc = db.get(Document, document_id)
    if not doc or doc.account_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    if doc.status != "ready":
        raise HTTPException(status_code=409, detail="Document not ready")
    job = enqueue_summarize(db, doc.id, user.id)
    return {"job_id": job.id, "status": job.status}


@router.delete("/{document_id}", status_code=204, dependencies=[Depends(require_csrf_or_guest)])
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> Response:
    """Delete a document and everything it owns."""
    if document_id == DEMO_DOC_ID:
        raise HTTPException(status_code=403, detail="The demo document cannot be deleted")

    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    if doc.meta and doc.meta.get("is_demo"):
        raise HTTPException(status_code=403, detail="The demo document cannot be deleted")
    if user:
        if doc.account_id != user.id:
            raise HTTPException(status_code=404, detail="Not found")
    elif not document_owned_by_guest(doc, guest_id):
        raise HTTPException(status_code=404, detail="Not found")

    storage_key = purge_document(db, doc)
    db.commit()

    purge_ingest_tmp(document_id)

    try:
        delete_object(storage_key)
    except Exception:
        logger.exception(
            "failed to delete storage object for document %s (%s)", document_id, storage_key
        )

    return Response(status_code=204)
