"""Multimodal chat helpers for image documents."""

from __future__ import annotations

import base64

from app.models import Document
from app.services.storage import fetch_object


def is_image_document(doc: Document) -> bool:
    return (doc.content_type or "").lower().startswith("image/")


def document_image_data_url(doc: Document) -> str:
    data = fetch_object(doc.storage_key)
    encoded = base64.standard_b64encode(data).decode("ascii")
    return f"data:{doc.content_type};base64,{encoded}"


def build_user_message(
    text: str,
    doc: Document,
    *,
    include_image: bool,
    current_page: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> str | list[dict]:
    # Page scope is enforced by retrieval — the model only sees scoped excerpts.
    _ = (current_page, page_start, page_end)
    if not include_image or not is_image_document(doc):
        return text
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": document_image_data_url(doc)}},
    ]
