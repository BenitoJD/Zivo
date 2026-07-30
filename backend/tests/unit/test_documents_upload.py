"""Upload content-type gate — the choke point that closes the chunked-upload
stored-XSS bypass (attacker declares text/html / image/svg+xml on the chunked
init endpoint; without sniffing + allowlist the bytes would later be served
inline via the presigned URL on GET /sources/{id}/file).

The security contract: the only content types that reach storage are pdf, docx,
pptx, application/json (internal article pages), or text/plain. None of those
execute inline script when served, so even before the presigned-URL attachment
forcing (Fix 6, defense-in-depth), an attacker cannot land an inline-renderable
type in object storage. XML and HTML uploads are normalized to text/plain.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.document_create import (
    ALLOWED_TYPES,
    assert_upload_content_type_allowed,
    normalize_upload_content_type,
)

# Anything a browser might render/execute inline — the gate must never let one
# of these reach storage. (text/plain is intentionally absent: it renders as
# literal text, no script execution.)
_INLINE_RENDERABLE = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "application/javascript",
    "text/javascript",
    "application/ecmascript",
}


def _gate(filename: str, content_type: str, data: bytes) -> str:
    """Run the full gate (sniff + allowlist) and return the type that would be
    stored, or re-raise the HTTPException if rejected."""
    resolved = normalize_upload_content_type(filename, content_type, data)
    assert_upload_content_type_allowed(resolved)
    return resolved


def test_html_header_is_rewritten_to_text_plain_not_stored_as_html():
    """The headline XSS case: HTML body + text/html header. The sniff must
    rewrite text/* to text/plain — the stored type is text/plain (renders as
    literal text, no script execution), never text/html."""
    resolved = _gate(
        "x.html", "text/html", b"<html><body><script>alert(1)</script></body></html>"
    )
    assert resolved == "text/plain"
    assert resolved in ALLOWED_TYPES
    assert resolved not in _INLINE_RENDERABLE


def test_svg_is_blocked():
    """SVG carries inline <script>; reject as an unsupported image (422)."""
    with pytest.raises(HTTPException) as exc_info:
        _gate("x.svg", "image/svg+xml", b"<?xml version='1.0'?><svg><script/>")
    assert exc_info.value.status_code == 422


def test_javascript_is_blocked():
    with pytest.raises(HTTPException) as exc_info:
        _gate("script", "application/javascript", b"alert(1)")
    assert exc_info.value.status_code == 415


def test_js_file_with_script_extension_allowed_as_text():
    resolved = _gate("notes.js", "application/javascript", b"const x = 1;\nexport { x };")
    assert resolved == "text/plain"
    assert resolved in ALLOWED_TYPES


def test_png_image_is_blocked_as_unsupported_feature():
    """Raster images are an explicit 422 (feature-not-available)."""
    with pytest.raises(HTTPException) as exc_info:
        _gate("photo.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert exc_info.value.status_code == 422


def test_no_inline_renderable_type_reaches_storage_for_attacker_inputs():
    """Fuzz-ish property: across attacker-chosen (filename, header, body)
    combinations, the resolved type is either rejected or lands in the safe
    allowlist — never an inline-renderable type."""
    cases = [
        # (filename, declared content_type, body bytes)
        ("a.html", "text/html", b"<html><script>x</script>"),
        ("a.xhtml", "application/xhtml+xml", b"<html/>"),
        ("a.svg", "image/svg+xml", b"<svg/>"),
        ("a.js", "application/javascript", b"alert(1)"),
        ("a.txt", "text/javascript", b"alert(1)"),  # header liar
        ("innocent.pdf", "text/html", b"<html>"),  # filename liar, html header
        ("none", "application/xml", b"<x/>"),
        ("none", "application/x-shockwave-flash", b"CWS\x09"),
    ]
    for filename, ct, body in cases:
        try:
            resolved = _gate(filename, ct, body)
        except HTTPException:
            continue  # rejected — fine
        assert resolved not in _INLINE_RENDERABLE, (
            f"inline-renderable type {resolved!r} reached storage for "
            f"({filename!r}, {ct!r})"
        )
        assert resolved in ALLOWED_TYPES


def test_pdf_allowed():
    assert _gate("doc.pdf", "application/pdf", b"%PDF-1.7\n%rest") == "application/pdf"


def test_docx_mislabelled_as_msword_is_corrected():
    """Browsers commonly send .docx as application/msword; the sniff fixes it."""
    assert (
        _gate("notes.docx", "application/msword", b"PK\x03\x04" + b"\x00" * 30)
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_pptx_mislabelled_as_zip_is_corrected():
    assert (
        _gate("deck.pptx", "application/zip", b"PK\x03\x04" + b"\x00" * 30)
        == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )


def test_text_plain_allowed():
    assert _gate("notes.txt", "text/plain", b"hello world") == "text/plain"


def test_json_file_allowed():
    body = b'{"title": "hello", "items": [1, 2]}'
    assert _gate("data.json", "application/json", body) == "application/json"


def test_xml_file_normalized_to_text_plain():
    assert _gate("feed.xml", "application/xml", b"<root><item>one</item></root>") == "text/plain"


def test_csv_file_allowed():
    assert _gate("data.csv", "text/csv", b"name,score\nalice,10") == "text/plain"


def test_python_source_allowed():
    assert _gate("main.py", "text/x-python", b"def main():\n    pass") == "text/plain"


def test_internal_article_json_allowed():
    body = b'{"pages": [{"page": 1, "text": "hello"}]}'
    assert _gate("notes.article", "application/json", body) == "application/json"


def test_legacy_doc_rejected_with_helpful_message():
    """Legacy .doc (OLE compound, not ZIP) is unsupported — error message guides
    the user toward .docx or PDF rather than failing generically."""
    with pytest.raises(HTTPException) as exc_info:
        normalize_upload_content_type(
            "old.doc", "application/msword", b"\xd0\xcf\x11\xe0" + b"\x00" * 32
        )
    assert exc_info.value.status_code == 415
    assert ".docx" in exc_info.value.detail


def test_legacy_ppt_rejected():
    with pytest.raises(HTTPException) as exc_info:
        normalize_upload_content_type(
            "old.ppt", "application/vnd.ms-powerpoint", b"\xd0\xcf\x11\xe0"
        )
    assert exc_info.value.status_code == 415
