"""Tests for multi-file document bundling."""

from __future__ import annotations

import fitz
import pytest
from fastapi import HTTPException

from app.services.document_bundle import build_document_bundle


def _mini_pdf(label: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), label)
    data = doc.tobytes()
    doc.close()
    return data


def test_merge_two_pdfs_into_one_document() -> None:
    files = [
        ("a.pdf", "application/pdf", _mini_pdf("Page A")),
        ("b.pdf", "application/pdf", _mini_pdf("Page B")),
    ]
    data, content_type, filename, meta = build_document_bundle(files)

    assert content_type == "application/pdf"
    assert "(+1 more)" in filename
    assert meta["bundle"] is True
    assert len(meta["source_files"]) == 2
    assert meta["page_count"] == 2

    with fitz.open(stream=data, filetype="pdf") as merged:
        assert merged.page_count == 2


def test_mixed_types_combine_into_json_pages() -> None:
    files = [
        ("notes.txt", "text/plain", b"First file line one.\n\nSecond paragraph."),
        ("more.txt", "text/plain", b"Second file content."),
    ]
    data, content_type, filename, meta = build_document_bundle(files)

    assert content_type == "application/json"
    assert filename.endswith(".bundle")
    assert meta["page_count"] >= 2
    assert b'"pages"' in data


def test_single_file_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        build_document_bundle([("only.pdf", "application/pdf", _mini_pdf("solo"))])
    assert exc.value.status_code == 400
