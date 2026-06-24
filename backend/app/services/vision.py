"""Multimodal chat helpers for image documents."""

from __future__ import annotations

import base64
import logging

from sqlalchemy.orm import Session

from app.models import Document
from app.services.storage import fetch_object

logger = logging.getLogger(__name__)

_META_IMAGE_DATA_URL = "image_data_url"
MAX_IMAGE_BYTES = 4 * 1024 * 1024


def is_image_document(doc: Document) -> bool:
    return (doc.content_type or "").lower().startswith("image/")


def document_image_data_url(doc: Document, db: Session | None = None) -> str | None:
    meta = dict(doc.meta or {})
    cached = meta.get(_META_IMAGE_DATA_URL)
    if isinstance(cached, str) and cached.startswith("data:"):
        return cached

    if (doc.size_bytes or 0) > MAX_IMAGE_BYTES:
        logger.warning("image document %s exceeds size cap (%s bytes)", doc.id, doc.size_bytes)
        return None

    data = fetch_object(doc.storage_key)
    if len(data) > MAX_IMAGE_BYTES:
        logger.warning(
            "fetched image for document %s exceeds cap (%s > %s bytes)",
            doc.id,
            len(data),
            MAX_IMAGE_BYTES,
        )
        return None

    encoded = base64.standard_b64encode(data).decode("ascii")
    url = f"data:{doc.content_type};base64,{encoded}"
    meta[_META_IMAGE_DATA_URL] = url
    doc.meta = meta
    if db is not None:
        db.add(doc)
        db.commit()
    return url


def build_user_message(
    text: str,
    doc: Document,
    *,
    include_image: bool,
    current_page: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    db: Session | None = None,
) -> str | list[dict]:
    # Page scope is enforced by retrieval — the model only sees scoped excerpts.
    _ = (current_page, page_start, page_end)
    if not include_image or not is_image_document(doc):
        return text
    image_url = document_image_data_url(doc, db=db)
    if not image_url:
        return text
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
