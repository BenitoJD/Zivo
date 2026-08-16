"""Shared helpers for creating documents from uploads and imports."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.models import Account, Document
from app.services.jobs import enqueue_ingest
from app.services.parse import count_document_pages
from app.services.parse_detect import evaluate_parse_detect
from app.services.presence import evaluate_presence
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
    "application/json",
    "text/plain",
}
# Safe text-like extensions (stored as text/plain; never inline-renderable).
_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".log",
        ".rst",
        ".jsonl",
        ".ndjson",
        ".xml",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".sql",
        ".tex",
        ".rtf",
        ".org",
        ".adoc",
        ".asciidoc",
        ".htm",
        ".html",
        ".css",
        ".py",
        ".rb",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".swift",
        ".kt",
        ".scala",
        ".php",
        ".pl",
        ".r",
        ".lua",
        ".vim",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".bat",
        ".env",
        ".properties",
        ".gradle",
        ".dockerfile",
        ".vue",
        ".svelte",
        ".tsx",
        ".ts",
        ".jsx",
        ".js",
    }
)
# Image study uploads are rejected until OCR ingest is production-ready.
ALLOWED_IMAGE_PREFIX = "image/"
_DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_CT = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_SNIFF_RULES = (
    Rule(when=(Pred("fmt", "eq", "pdf"),), action="pdf"),
    Rule(when=(Pred("pdf_name", "truthy"),), action="pdf"),
    Rule(when=(Pred("pptx_name", "truthy"),), action="pptx"),
    Rule(when=(Pred("zip_pres", "truthy"),), action="pptx"),
    Rule(when=(Pred("docx_name", "truthy"),), action="docx"),
    Rule(when=(Pred("zip_word", "truthy"),), action="docx"),
    Rule(when=(Pred("ppt_legacy", "truthy"),), action="reject_ppt"),
    Rule(when=(Pred("doc_zip", "truthy"),), action="docx"),
    Rule(when=(Pred("doc_legacy", "truthy"),), action="reject_doc"),
    Rule(when=(Pred("xhtml", "truthy"),), action="text"),
    Rule(when=(Pred("json", "truthy"),), action="json"),
    Rule(when=(Pred("xml", "truthy"),), action="text"),
    Rule(when=(Pred("text_ext", "truthy"),), action="text"),
    Rule(when=(Pred("text_ct", "truthy"),), action="text"),
    Rule(when=(), action="passthrough"),
)

_COUNTABLE_RULES = (
    Rule(when=(Pred("fmt", "eq", "pdf"),), action="count"),
    Rule(when=(Pred("pdf_ct", "truthy"),), action="count"),
    Rule(when=(Pred("docx_ct", "truthy"),), action="count"),
    Rule(when=(Pred("pptx_ct", "truthy"),), action="count"),
    Rule(when=(Pred("text_ct", "truthy"),), action="count"),
    Rule(when=(Pred("json_ct", "truthy"),), action="count"),
    Rule(when=(), action="skip"),
)


def _raise(exc: BaseException) -> None:
    raise exc


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
    detect = evaluate_parse_detect(filename=name, header=data[:8])
    is_zip = data[:2] == b"PK"
    hit = first_match(
        _SNIFF_RULES,
        {
            "fmt": detect.action,
            "pdf_name": name.endswith(".pdf"),
            "pptx_name": name.endswith(".pptx"),
            "zip_pres": is_zip and "presentationml" in ct,
            "docx_name": name.endswith(".docx"),
            "zip_word": is_zip and ("wordprocessingml" in ct or ct == "application/msword"),
            "ppt_legacy": name.endswith(".ppt") or "ms-powerpoint" in ct,
            "doc_zip": (name.endswith(".doc") or ct == "application/msword") and is_zip,
            "doc_legacy": name.endswith(".doc") or ct == "application/msword",
            "xhtml": ct in ("application/xhtml+xml",),
            "json": name.endswith(".json") or ct == "application/json",
            "xml": name.endswith(".xml") or ct in ("application/xml", "text/xml"),
            "text_ext": any(name.endswith(ext) for ext in _TEXT_EXTENSIONS),
            "text_ct": ct.startswith("text/"),
        },
    )
    return apply(
        hit.action,
        {
            "pdf": lambda: "application/pdf",
            "pptx": lambda: _PPTX_CT,
            "docx": lambda: _DOCX_CT,
            "reject_ppt": lambda: _raise(
                HTTPException(
                    status_code=415,
                    detail="Legacy .ppt isn't supported: save as .pptx or PDF and upload again.",
                )
            ),
            "reject_doc": lambda: _raise(
                HTTPException(
                    status_code=415,
                    detail="Legacy .doc isn't supported: save as .docx or PDF and upload again.",
                )
            ),
            "text": lambda: "text/plain",
            "json": lambda: "application/json",
            "passthrough": lambda: ct,
        },
    )


def assert_upload_content_type_allowed(resolved_ct: str) -> None:
    """Apply the allowlist + image reject to a resolved content type."""
    apply(
        first_match(
            (
                Rule(when=(Pred("image", "truthy"),), action="image"),
                Rule(when=(Pred("allowed", "falsey"),), action="unsupported"),
                Rule(when=(), action="ok"),
            ),
            {
                "image": resolved_ct.startswith(ALLOWED_IMAGE_PREFIX),
                "allowed": resolved_ct in ALLOWED_TYPES,
            },
        ).action,
        {
            "image": lambda: _raise(
                HTTPException(
                    status_code=422,
                    detail="Image study isn't available yet: upload a PDF, Word, PowerPoint, text file, or paste text.",
                )
            ),
            "unsupported": lambda: _raise(
                HTTPException(status_code=415, detail="Unsupported file type")
            ),
            "ok": lambda: None,
        },
    )


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

    def _for_user() -> tuple[int, str | None]:
        used = db.query(func.coalesce(func.sum(Document.size_bytes), 0)).filter(
            Document.account_id == user.id
        ).scalar()
        return int(used or 0), None

    def _guest_cap(doc_guest_id: str) -> None:
        non_image_count = (
            db.query(func.count(Document.id))
            .filter(Document.account_id.is_(None))
            .filter(Document.meta["guest_id"].astext == doc_guest_id)
            .filter(~Document.content_type.startswith("image/"))
            .scalar()
        )
        pick(
            int(non_image_count or 0) >= settings.guest_document_limit,
            lambda: _raise(HTTPException(status_code=409, detail="Sign in to add more documents")),
            lambda: None,
        )

    def _for_guest() -> tuple[int, str | None]:
        apply(
            evaluate_presence(guest_id).action,
            {
                "missing": lambda: _raise(
                    ValueError("guest_id is required to create an anonymous document")
                ),
                "empty": lambda: _raise(
                    ValueError("guest_id is required to create an anonymous document")
                ),
                "ok": lambda: None,
            },
        )
        pick(counts_toward_guest_cap, lambda: _guest_cap(guest_id), lambda: None)
        return guest_storage_used(db, guest_id), guest_id

    used, doc_guest_id = pick(bool(user), _for_user, _for_guest)
    pick(
        used + size_bytes > settings.storage_limit_bytes,
        lambda: _raise(HTTPException(status_code=413, detail="Storage quota exceeded")),
        lambda: None,
    )
    return doc_guest_id


def _maybe_page_count(doc_meta: dict, content_type: str, data: bytes) -> None:
    ct_lower = (content_type or "").lower()
    detect = evaluate_parse_detect(header=data[:8])
    hit = first_match(
        _COUNTABLE_RULES,
        {
            "fmt": detect.action,
            "pdf_ct": "pdf" in ct_lower or data[:4] == b"%PDF",
            "docx_ct": "wordprocessingml" in ct_lower or "msword" in ct_lower,
            "pptx_ct": "presentationml" in ct_lower,
            "text_ct": ct_lower.startswith("text/"),
            "json_ct": ct_lower == "application/json",
        },
    )

    def _count() -> None:
        try:
            doc_meta["page_count"] = count_document_pages(content_type, data)
        except Exception:
            # Upload must succeed even if page counting fails; ingest will set it.
            pass

    apply(hit.action, {"count": _count, "skip": lambda: None})


def _finish_document(
    db: Session,
    *,
    user: Account | None,
    filename: str,
    content_type: str,
    size_bytes: int,
    storage_key: str,
    doc_meta: dict,
    is_image: bool,
) -> Document:
    doc = Document(
        account_id=getattr(user, "id", None),
        slug=slugify_filename(filename),
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        status=choose(is_image, "indexing", "pending"),
        meta=doc_meta,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    pick(is_image, lambda: enqueue_ingest(db, doc.id), lambda: None)
    return doc


def _assert_size(size_bytes: int) -> None:
    settings = get_settings()
    pick(
        size_bytes > settings.max_upload_bytes,
        lambda: _raise(
            HTTPException(
                status_code=413,
                detail=f"File too large (max {settings.max_upload_bytes // _GB}GB)",
            )
        ),
        lambda: None,
    )


def _with_guest_meta(meta: dict | None, doc_guest_id: str | None) -> dict:
    doc_meta: dict = dict(meta or {})
    pick(
        bool(doc_guest_id),
        lambda: doc_meta.__setitem__("guest_id", doc_guest_id),
        lambda: None,
    )
    return doc_meta


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
    _assert_size(len(data))
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
    storage_key = save_upload(getattr(user, "id", None), filename, data, content_type)
    doc_meta = _with_guest_meta(meta, doc_guest_id)
    pick(not is_image, lambda: _maybe_page_count(doc_meta, content_type, data), lambda: None)
    return _finish_document(
        db,
        user=user,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        storage_key=storage_key,
        doc_meta=doc_meta,
        is_image=is_image,
    )


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
    _assert_size(size_bytes)
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
    doc_guest_id = apply(
        evaluate_presence(guest_id_override).action,
        {
            "ok": lambda: guest_id_override,
            "empty": lambda: assert_storage_available(
                db,
                user=user,
                guest_id=guest_id,
                size_bytes=size_bytes,
                counts_toward_guest_cap=not is_image,
            ),
            "missing": lambda: assert_storage_available(
                db,
                user=user,
                guest_id=guest_id,
                size_bytes=size_bytes,
                counts_toward_guest_cap=not is_image,
            ),
        },
    )
    doc_meta = _with_guest_meta(meta, doc_guest_id)
    pick(not is_image, lambda: _maybe_page_count(doc_meta, content_type, fetched), lambda: None)
    return _finish_document(
        db,
        user=user,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        doc_meta=doc_meta,
        is_image=is_image,
    )
