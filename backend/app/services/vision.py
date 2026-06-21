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
) -> str | list[dict]:
    page_context = ""
    if current_page is not None and current_page > 0:
        page_context = f"[The user is currently viewing page {current_page}.]\n\n"
    if not include_image or not is_image_document(doc):
        return f"{page_context}{text}"
    return [
        {"type": "text", "text": f"{page_context}{text}"},
        {"type": "image_url", "image_url": {"url": document_image_data_url(doc)}},
    ]
