"""Merge multiple uploads into one study document."""

from __future__ import annotations

import fitz
from fastapi import HTTPException

from app.services.document_create import (
    assert_upload_content_type_allowed,
    normalize_upload_content_type,
)
from app.services.parse import count_document_pages, parse_document
from app.services.web_import import encode_article_pages

_MAX_BUNDLE_FILES = 20
_PDF_CT = "application/pdf"


def _bundle_display_filename(filenames: list[str], *, merged_pdf: bool) -> str:
    first = filenames[0]
    if len(filenames) == 1:
        return first
    extra = len(filenames) - 1
    stem = first.rsplit(".", 1)[0] if "." in first else first
    suffix = f" (+{extra} more)"
    if merged_pdf:
        return f"{stem}{suffix}.pdf"
    return f"{stem}{suffix}.bundle"


def _merge_pdf_bytes(parts: list[bytes]) -> bytes:
    merged = fitz.open()
    try:
        for part in parts:
            src = fitz.open(stream=part, filetype="pdf")
            try:
                merged.insert_pdf(src)
            finally:
                src.close()
        return merged.tobytes()
    finally:
        merged.close()


def _combine_parsed_pages(
    files: list[tuple[str, str, bytes]],
) -> tuple[bytes, int]:
    pages: list[dict] = []
    page_num = 1
    for filename, content_type, data in files:
        for item in parse_document(content_type, data):
            text = str(item.get("text") or "").strip()
            entry: dict = {"page": page_num, "text": text}
            if item.get("sparse_text"):
                entry["sparse_text"] = True
            pages.append(entry)
            page_num += 1
    if not pages:
        pages = [{"page": 1, "text": ""}]
    return encode_article_pages(pages), len(pages)


def build_document_bundle(
    raw_files: list[tuple[str, str, bytes]],
) -> tuple[bytes, str, str, dict]:
    """Validate, merge, and return (data, content_type, filename, meta)."""
    if len(raw_files) < 2:
        raise HTTPException(status_code=400, detail="Select at least 2 files to combine")
    if len(raw_files) > _MAX_BUNDLE_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files (max {_MAX_BUNDLE_FILES})",
        )

    normalized: list[tuple[str, str, bytes]] = []
    source_files_meta: list[dict] = []
    for filename, content_type, data in raw_files:
        if not data:
            raise HTTPException(status_code=400, detail=f"File is empty: {filename or 'document'}")
        resolved = normalize_upload_content_type(filename or "document", content_type, data)
        assert_upload_content_type_allowed(resolved)
        normalized.append((filename or "document", resolved, data))
        source_files_meta.append(
            {
                "filename": filename or "document",
                "content_type": resolved,
                "page_count": count_document_pages(resolved, data),
            }
        )

    filenames = [name for name, _, _ in normalized]
    all_pdf = all(ct == _PDF_CT for _, ct, _ in normalized)

    if all_pdf:
        merged = _merge_pdf_bytes([data for _, _, data in normalized])
        display_name = _bundle_display_filename(filenames, merged_pdf=True)
        meta = {
            "bundle": True,
            "source_files": source_files_meta,
            "page_count": count_document_pages(_PDF_CT, merged),
        }
        return merged, _PDF_CT, display_name, meta

    data, page_count = _combine_parsed_pages(normalized)
    display_name = _bundle_display_filename(filenames, merged_pdf=False)
    meta = {
        "bundle": True,
        "source_files": source_files_meta,
        "page_count": page_count,
    }
    return data, "application/json", display_name, meta
