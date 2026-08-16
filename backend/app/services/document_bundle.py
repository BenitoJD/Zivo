"""Merge multiple uploads into one study document."""

from __future__ import annotations

import fitz
from fastapi import HTTPException

from app.engine_runtime import choose, pick
from app.services.document_create import (
    assert_upload_content_type_allowed,
    normalize_upload_content_type,
)
from app.services.parse import count_document_pages, parse_document
from app.services.session_design import plan_bundle_upload
from app.services.web_import import encode_article_pages

_PDF_CT = "application/pdf"


def _bundle_display_filename(filenames: list[str], *, merged_pdf: bool) -> str:
    first = filenames[0]
    extra = len(filenames) - 1
    stem = pick("." in first, lambda: first.rsplit(".", 1)[0], lambda: first)
    suffix = f" (+{extra} more)"
    return pick(
        len(filenames) == 1,
        lambda: first,
        lambda: choose(merged_pdf, f"{stem}{suffix}.pdf", f"{stem}{suffix}.bundle"),
    )


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
            pick(bool(item.get("sparse_text")), lambda: entry.__setitem__("sparse_text", True), lambda: None)
            pages.append(entry)
            page_num += 1
    return encode_article_pages(pages or [{"page": 1, "text": ""}]), len(pages or [{"page": 1, "text": ""}])


def build_document_bundle(
    raw_files: list[tuple[str, str, bytes]],
) -> tuple[bytes, str, str, dict]:
    """Validate, merge, and return (data, content_type, filename, meta)."""
    bundle = plan_bundle_upload()

    def _too_few() -> None:
        raise HTTPException(status_code=400, detail="Select at least 2 files to combine")

    def _too_many() -> None:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files (max {bundle.max_files})",
        )

    pick(len(raw_files) < bundle.min_files, _too_few, lambda: None)
    pick(len(raw_files) > bundle.max_files, _too_many, lambda: None)

    normalized: list[tuple[str, str, bytes]] = []
    source_files_meta: list[dict] = []
    for filename, content_type, data in raw_files:
        def _empty() -> None:
            raise HTTPException(status_code=400, detail=f"File is empty: {filename or 'document'}")

        pick(not data, _empty, lambda: None)
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

    def _pdf_bundle() -> tuple[bytes, str, str, dict]:
        merged = _merge_pdf_bytes([data for _, _, data in normalized])
        display_name = _bundle_display_filename(filenames, merged_pdf=True)
        meta = {
            "bundle": True,
            "source_files": source_files_meta,
            "page_count": count_document_pages(_PDF_CT, merged),
        }
        return merged, _PDF_CT, display_name, meta

    def _parsed_bundle() -> tuple[bytes, str, str, dict]:
        data, page_count = _combine_parsed_pages(normalized)
        display_name = _bundle_display_filename(filenames, merged_pdf=False)
        meta = {
            "bundle": True,
            "source_files": source_files_meta,
            "page_count": page_count,
        }
        return data, "application/json", display_name, meta

    return pick(all_pdf, _pdf_bundle, _parsed_bundle)
