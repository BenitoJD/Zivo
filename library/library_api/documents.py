import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.document_access import require_document, require_document_source
from app.db import get_db
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.models import Account, Document, DocumentChunk, User
from app.services.auth import get_current_user, get_optional_user, require_csrf, require_csrf_or_guest
from app.services.document_bundle import build_document_bundle
from app.services.document_create import (
    assert_upload_content_type_allowed,
    create_document_from_storage,
    create_document_record,
    normalize_upload_content_type,
)
from app.services.document_purge import purge_document, purge_ingest_tmp
from app.services.guest import document_owned_by_guest
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.jobs import enqueue_summarize
from app.services.presence import evaluate_presence
from app.services.storage import delete_object, get_object_meta, presigned_get_url
from app.services.web_import import (
    WebImportError,
    article_filename,
    article_from_pasted_text,
    build_document_meta,
    encode_article_pages,
    fetch_and_extract,
    normalize_public_url,
    paginate_reader_text,
    source_domain,
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


def _raise_http(status: int, detail: str) -> None:
    raise HTTPException(status_code=status, detail=detail)


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
    job_id: uuid.UUID | None = None
    status: str
    summary: str | None = None


class ImportUrlIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)


class ImportTextIn(BaseModel):
    text: str = Field(min_length=40, max_length=500_000)
    title: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)


class FromObjectIn(BaseModel):
    object_id: uuid.UUID


class ImportGithubIn(BaseModel):
    github_url: str = Field(min_length=8, max_length=2048)


@router.get("/demo", response_model=DocumentOut)
def get_demo_document(db: Session = Depends(get_db)) -> Document:
    doc = db.get(Document, DEMO_DOC_ID)
    apply(
        evaluate_presence(doc).action,
        {
            "missing": lambda: _raise_http(404, "Demo document not seeded"),
            "empty": lambda: _raise_http(404, "Demo document not seeded"),
            "ok": lambda: None,
        },
    )
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> list[Document]:
    def _account_docs() -> list[Document]:
        return (
            db.query(Document)
            .filter(Document.account_id == user.id)
            .order_by(Document.created_at.desc())
            .limit(_LIST_DOCUMENTS_LIMIT)
            .all()
        )

    def _guest_docs() -> list[Document]:
        return (
            db.query(Document)
            .filter(Document.account_id.is_(None))
            .filter(Document.meta["guest_id"].astext == guest_id)
            .order_by(Document.created_at.desc())
            .limit(_LIST_DOCUMENTS_LIMIT)
            .all()
        )

    docs = apply(
        first_match(
            (
                Rule(when=(Pred("has_user", "truthy"),), action="account"),
                Rule(when=(Pred("has_guest", "falsey"),), action="empty"),
                Rule(when=(), action="guest"),
            ),
            {"has_user": user is not None, "has_guest": guest_id is not None},
        ).action,
        {
            "account": _account_docs,
            "empty": lambda: [],
            "guest": _guest_docs,
        },
    )
    return [_refresh_prep_state(db, doc) for doc in docs]


def _refresh_prep_state(db: Session, doc: Document) -> Document:
    """Recover a stranded background-prep doc and recompute its prep progress.

    Cheap for the common case (ready/failed docs short-circuit on
    ``is_background_prep``). Only docs still cooking hit the recovery + compute
    path, the same work the detail view already does on open.
    """
    from app.services.background_prep import (
        compute_prep_progress,
        is_background_prep,
        maybe_recover_stuck_background_prep,
    )
    from app.services.rag_window import maybe_recover_stuck_indexing

    try:
        maybe_recover_stuck_indexing(db, doc)
        maybe_recover_stuck_background_prep(db, doc)

        def _update() -> None:
            db.refresh(doc)
            progress = compute_prep_progress(db, doc)
            doc.index_progress = int(progress["overall_pct"])
            meta = dict(doc.meta or {})
            meta["prep_progress"] = progress
            doc.meta = meta
            db.add(doc)
            db.commit()
            db.refresh(doc)

        pick(is_background_prep(doc), _update, lambda: None)
    except Exception:
        logger.exception("prep state refresh failed for document %s", doc.id)
    return doc


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
    doc = require_document_source(db, document_id, user, guest_id)

    def _demo_text() -> Response:
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.page_start.asc())
            .all()
        )
        text_body = "\n\n".join(filter(None, (c.text for c in chunks)))
        return Response(content=text_body.encode("utf-8"), media_type="text/plain")

    return pick(
        bool(doc.meta) and bool(doc.meta.get("is_demo")),
        _demo_text,
        lambda: RedirectResponse(presigned_get_url(doc.storage_key)),
    )


@router.get("/{document_id}/pages")
def get_document_pages(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Return per-page text for the reader / page-picker previews.

    Prefer indexed chunks when present. Before page selection (no ingest yet),
    fall back to ``parse_document`` so DOCX/PPTX/text still get usable
    thumbnails. PDFs use client-side pdf.js instead.
    """
    doc = require_document_source(db, document_id, user, guest_id)
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
        pages[c.page_start] = pick(
            bool(existing),
            lambda existing=existing, text=c.text: f"{existing}\n\n{text}",
            lambda text=c.text: text,
        )

    def _from_chunks() -> dict:
        return {"pages": [{"page": p, "text": t} for p, t in sorted(pages.items())]}

    def _parse_fallback() -> dict:
        from app.services.parse import parse_document
        from app.services.storage import fetch_object

        try:
            raw = fetch_object(doc.storage_key)
            parsed = parse_document(doc.content_type or "", raw)
        except Exception:
            logger.exception("page preview parse failed for %s", document_id)
            return {"pages": []}
        return {
            "pages": [
                {"page": int(item.get("page") or i), "text": str(item.get("text") or "")}
                for i, item in enumerate(parsed, start=1)
            ]
        }

    return pick(bool(pages), _from_chunks, _parse_fallback)


async def _read_upload_capped(request: Request, file: UploadFile) -> bytes:
    max_bytes = settings.max_upload_bytes
    content_length = request.headers.get("content-length")

    def _check_declared() -> None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=413, detail="File too large") from exc
        pick(declared > max_bytes, lambda: _raise_http(413, "File too large"), lambda: None)

    pick(content_length is not None, _check_declared, lambda: None)

    chunks: list[bytes] = []
    total = [0]

    async def _step() -> bytes:
        chunk = await file.read(_READ_CHUNK_BYTES)

        async def _more() -> bytes:
            total[0] += len(chunk)
            pick(total[0] > max_bytes, lambda: _raise_http(413, "File too large"), lambda: None)
            chunks.append(chunk)
            return await _step()

        async def _done() -> bytes:
            return b"".join(chunks)

        return await pick(not chunk, _done, _more)

    return await _step()


async def _read_files_capped(files: list[UploadFile]) -> list[tuple[str, str, bytes]]:
    max_bytes = settings.max_upload_bytes
    payloads: list[tuple[str, str, bytes]] = []
    total = [0]

    async def _read_one(upload: UploadFile) -> None:
        chunks: list[bytes] = []

        async def _step() -> None:
            chunk = await upload.read(_READ_CHUNK_BYTES)

            async def _more() -> None:
                total[0] += len(chunk)
                pick(total[0] > max_bytes, lambda: _raise_http(413, "File too large"), lambda: None)
                chunks.append(chunk)
                await _step()

            async def _done() -> None:
                payloads.append(
                    (
                        upload.filename or "document",
                        upload.content_type or "",
                        b"".join(chunks),
                    )
                )

            await pick(not chunk, _done, _more)

        await _step()

    for upload in files:
        await _read_one(upload)
    return payloads


@router.post(
    "/from-object",
    response_model=DocumentOut,
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def create_from_storage_object(
    body: FromObjectIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> Document:
    """Register a storage object as a product document and enqueue ingest."""
    meta = get_object_meta(object_id=body.object_id)
    owner_account = meta.get("account_id")
    owner_guest = meta.get("guest_id")
    apply(
        first_match(
            (
                Rule(
                    when=(Pred("has_user", "truthy"), Pred("owner_ok", "falsey")),
                    action="missing",
                ),
                Rule(when=(Pred("has_user", "truthy"),), action="ok"),
                Rule(
                    when=(Pred("has_guest", "truthy"), Pred("guest_ok", "falsey")),
                    action="missing",
                ),
                Rule(when=(Pred("has_guest", "truthy"),), action="ok"),
                Rule(when=(), action="unauth"),
            ),
            {
                "has_user": user is not None,
                "has_guest": bool(guest_id),
                "owner_ok": user is not None and owner_account == str(user.id),
                "guest_ok": bool(guest_id) and owner_guest == guest_id,
            },
        ).action,
        {
            "ok": lambda: None,
            "missing": lambda: _raise_http(404, "Object not found"),
            "unauth": lambda: _raise_http(401, "Not authenticated"),
        },
    )

    def _run() -> Document:
        return create_document_from_storage(
            db,
            user=user,
            guest_id=guest_id,
            filename=meta["filename"],
            content_type=meta["content_type"],
            storage_key=meta["storage_key"],
            size_bytes=int(meta["size_bytes"]),
            guest_id_override=owner_guest,
        )

    return await asyncio.to_thread(_run)


@router.post("", response_model=DocumentOut, dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)])
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> Document:
    data = await _read_upload_capped(request, file)

    ct = normalize_upload_content_type(
        file.filename or "document", file.content_type or "", data
    )
    assert_upload_content_type_allowed(ct)

    return await asyncio.to_thread(
        create_document_record,
        db,
        user=user,
        guest_id=guest_id,
        filename=file.filename or "document",
        content_type=ct,
        data=data,
    )


@router.post(
    "/upload-bundle",
    response_model=DocumentOut,
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def upload_document_bundle(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> Document:
    raw_files = await _read_files_capped(files)
    data, content_type, filename, meta = await asyncio.to_thread(build_document_bundle, raw_files)
    return await asyncio.to_thread(
        create_document_record,
        db,
        user=user,
        guest_id=guest_id,
        filename=filename,
        content_type=content_type,
        data=data,
        meta=meta,
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
        article = await pick(
            is_youtube,
            lambda: asyncio.to_thread(fetch_youtube_transcript, body.url),
            lambda: fetch_and_extract(body.url),
        )
    except WebImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pages = paginate_reader_text(article.text)
    data = encode_article_pages(pages)
    meta = build_document_meta(article, source_type=choose(is_youtube, "youtube", "url"))
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
    meta = build_document_meta(
        article, source_type=choose(not article.source_url, "paste", "url")
    )
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
    try:
        url = normalize_public_url(body.github_url.strip())
    except WebImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    host = source_domain(url).lower()
    github_ok = (
        host == "github.com"
        or host.endswith(".github.com")
        or host == "githubusercontent.com"
        or host.endswith(".githubusercontent.com")
    )
    pick(github_ok, lambda: None, lambda: _raise_http(422, "Not a GitHub URL"))
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
    apply(
        first_match(
            (
                Rule(when=(Pred("found", "falsey"),), action="missing"),
                Rule(when=(Pred("owner", "falsey"),), action="missing"),
                Rule(when=(Pred("ready", "falsey"),), action="conflict"),
                Rule(when=(), action="ok"),
            ),
            {
                "found": doc is not None,
                "owner": doc is not None and doc.account_id == user.id,
                "ready": doc is not None and doc.status == "ready",
            },
        ).action,
        {
            "missing": lambda: _raise_http(404, "Not found"),
            "conflict": lambda: _raise_http(409, "Document not ready"),
            "ok": lambda: None,
        },
    )
    from app.services.source_fingerprint import chunks_fingerprint, is_artifact_stale

    meta = pick(isinstance(doc.meta, dict), lambda: doc.meta, lambda: {})
    cached_summary = meta.get("summary")

    def _cached() -> dict:
        return {"job_id": None, "status": "ready", "summary": cached_summary}

    def _enqueue() -> dict:
        _ = chunks_fingerprint(db, document_id)
        job = enqueue_summarize(db, doc.id, user.id)
        return {"job_id": job.id, "status": job.status}

    fresh = (
        isinstance(cached_summary, str)
        and bool(cached_summary.strip())
        and not is_artifact_stale(db, document_id, "summary")
    )
    return pick(fresh, _cached, _enqueue)


@router.delete("/{document_id}", status_code=204, dependencies=[Depends(require_csrf_or_guest)])
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> Response:
    """Delete a document and everything it owns."""
    pick(
        document_id == DEMO_DOC_ID,
        lambda: _raise_http(403, "The demo document cannot be deleted"),
        lambda: None,
    )

    doc = db.get(Document, document_id)
    apply(
        evaluate_presence(doc).action,
        {
            "missing": lambda: _raise_http(404, "Not found"),
            "empty": lambda: _raise_http(404, "Not found"),
            "ok": lambda: None,
        },
    )
    pick(
        bool(doc.meta) and bool(doc.meta.get("is_demo")),
        lambda: _raise_http(403, "The demo document cannot be deleted"),
        lambda: None,
    )
    apply(
        first_match(
            (
                Rule(
                    when=(Pred("has_user", "truthy"), Pred("owner", "falsey")),
                    action="missing",
                ),
                Rule(when=(Pred("has_user", "truthy"),), action="ok"),
                Rule(when=(Pred("guest_ok", "falsey"),), action="missing"),
                Rule(when=(), action="ok"),
            ),
            {
                "has_user": user is not None,
                "owner": user is not None and doc.account_id == user.id,
                "guest_ok": document_owned_by_guest(doc, guest_id),
            },
        ).action,
        {
            "missing": lambda: _raise_http(404, "Not found"),
            "ok": lambda: None,
        },
    )

    storage_key = purge_document(db, doc)

    meta = doc.meta or {}
    pick(
        meta.get("ingest_kind") == "newspaper",
        lambda: db.execute(
            text(
                """
                UPDATE qb.newspaper_edition
                SET status = 'purged', updated_at = now()
                WHERE document_id = :doc AND status <> 'purged'
                """
            ),
            {"doc": doc.id},
        ),
        lambda: None,
    )
    db.commit()

    purge_ingest_tmp(document_id)

    try:
        delete_object(storage_key)
    except Exception:
        logger.exception(
            "failed to delete storage object for document %s (%s)", document_id, storage_key
        )

    return Response(status_code=204)
