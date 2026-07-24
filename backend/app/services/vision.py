"""Multimodal chat helpers for image documents and empty-page vision glance."""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import Document
from app.services.storage import fetch_object

logger = logging.getLogger(__name__)

_META_IMAGE_DATA_URL = "image_data_url"
MAX_IMAGE_BYTES = 4 * 1024 * 1024

_PAGE_VISION_JUDGE_SYSTEM = (
    "You glance at one page image from a study PDF. Decide only whether it has "
    "useful study content a learner could be quizzed on (prose, slides with ideas, "
    "labeled diagrams, equations, code). Blank, decorative, cover, TOC, index, or "
    "unreadable noise is not useful. Return JSON only."
)
_PAGE_VISION_JUDGE_USER = (
    "Does this page have useful study content? Reply with exactly one JSON object: "
    '{"usable": <bool>, "rationale": "<one short sentence>"}'
)


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


def _page_image_data_url(doc: Document, page_number: int) -> str | None:
    """Build a data URL for one PDF page (or the whole image document)."""
    if is_image_document(doc):
        return document_image_data_url(doc)

    raw = fetch_object(doc.storage_key)
    ct = (doc.content_type or "").lower()
    if "pdf" not in ct and raw[:4] != b"%PDF":
        return None

    from app.services.parse import render_pdf_page_png

    png = render_pdf_page_png(raw, page_number, max_bytes=MAX_IMAGE_BYTES)
    if not png:
        return None
    encoded = base64.standard_b64encode(png).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _parse_usable_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text_block = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_block, re.DOTALL)
    if fence:
        text_block = fence.group(1)
    else:
        start = text_block.find("{")
        end = text_block.rfind("}")
        if start >= 0 and end > start:
            text_block = text_block[start : end + 1]
        else:
            return None
    try:
        parsed = json.loads(text_block)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def judge_page_has_content(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
) -> dict[str, Any]:
    """One vision glance: does this empty-text page have useful study content?

    Never transcribes. Cached per document storage key + page so retries never rebill.
    """
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put
    from app.services.llm_registry import vision_chat_model_id
    from app.services.llm_router import acomplete_chat
    from app.services.llm_sync import run_coro_in_worker

    doc = db.get(Document, document_id)
    if not doc:
        return {"usable": False, "rationale": "Document missing."}

    cache_key = content_hash_key(
        "page_vision_judge",
        doc.storage_key or str(doc.id),
        int(page_number),
    )
    hit = cache_get(db, kind="page_vision_judge", cache_key=cache_key)
    if isinstance(hit, dict) and "usable" in hit:
        return {"usable": bool(hit.get("usable")), "rationale": str(hit.get("rationale") or "")}

    image_url = _page_image_data_url(doc, int(page_number))
    if not image_url:
        verdict = {"usable": False, "rationale": "Could not render page for vision glance."}
        cache_put(db, kind="page_vision_judge", cache_key=cache_key, value=verdict)
        return verdict

    content: list[dict] = [
        {"type": "text", "text": _PAGE_VISION_JUDGE_USER},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    try:
        raw = run_coro_in_worker(
            acomplete_chat(
                [
                    {"role": "system", "content": _PAGE_VISION_JUDGE_SYSTEM},
                    {"role": "user", "content": content},
                ],
                db,
                model_id=vision_chat_model_id(db),
                require_vision=True,
                log_tag="page_vision_judge",
                document_id=doc.id,
            )
        )
    except Exception:
        logger.warning(
            "page vision judge failed for doc %s page %s",
            document_id,
            page_number,
            exc_info=True,
        )
        return {"usable": False, "rationale": "Vision glance failed."}

    parsed = _parse_usable_json(raw or "")
    if not parsed:
        verdict = {"usable": False, "rationale": "Vision glance returned no usable judgment."}
    else:
        verdict = {
            "usable": bool(parsed.get("usable")),
            "rationale": str(parsed.get("rationale") or "").strip(),
        }
    cache_put(db, kind="page_vision_judge", cache_key=cache_key, value=verdict)
    return verdict
