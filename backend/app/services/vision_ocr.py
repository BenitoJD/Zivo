"""Vision OCR Fallback — transcribe sparse/empty PDF pages with a vision LLM.

Design: scanned pages, image-heavy pages, or text layers that failed to
extract would otherwise be lost as "blank". When the normal text pipeline
yields sparse text for a page, this renders the page to PNG and asks a
vision-capable LLM to transcribe the actual content, so the learner still
gets the material. Mirrors the page_vision_judge plumbing (render → vision
call → cache) but *transcribes* instead of just judging.

Rules (engine policy, ADR 0004):
  * only called for pages below the sparse threshold (never the hot path);
  * cached per document storage key + page + model, so a retry never re-bills;
  * if the vision call fails or the page is genuinely blank, return "" and
    let the normal blank-page handling proceed — never block the ingest.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.models import Document
from app.services.content_worthiness import (
    plan_vision_ocr_cache,
    should_revalidate_ocr_blank,
)
from app.services.llm_registry import vision_chat_model_id
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker

logger = logging.getLogger(__name__)

# Successful transcriptions last a month (immutable page image). Whether a
# cached BLANK is trusted is Content Worthiness policy, not this plumbing.

_OCR_SYSTEM = (
    "You are a precise OCR engine for study material. Transcribe the text "
    "content in the image EXACTLY as written: every sentence, heading, "
    "figure caption, and label. Preserve paragraph breaks. Do not add, "
    "summarize, or interpret. If the image is blank or contains no text, "
    "return exactly: BLANK"
)

_OCR_USER = (
    "Transcribe all readable text from this page. Keep the exact wording. "
    "If there is no text, reply with exactly BLANK."
)


def _page_image_url(db: Session, doc: Document, page_number: int) -> str | None:
    """Render the page to a data URL (reuses the judge's renderer)."""
    from app.services.vision import _page_image_data_url

    return _page_image_data_url(doc, int(page_number))


def transcribe_page_with_vision(
    db: Session,
    document_id: object,
    page_number: int,
) -> str:
    """OCR-transcribe a page via a vision LLM. Returns "" when blank/failed.

    Cached per (storage_key, page) so a worker retry never re-bills. On any
    vision failure the page is treated as blank (""), matching the old
    no-extraction behavior — the ingest never blocks on this.
    """
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    doc = db.get(Document, document_id)
    return pick(not doc, lambda: "", lambda: _transcribe(db, doc, document_id, page_number, cache_get, cache_put, content_hash_key))


def _transcribe(db, doc, document_id, page_number, cache_get, cache_put, content_hash_key) -> str:
    model_id = vision_chat_model_id(db)
    cache_key = content_hash_key(
        "vision_ocr", doc.storage_key or str(doc.id), int(page_number),
        str(model_id), _OCR_SYSTEM, _OCR_USER,
    )
    cache_plan = plan_vision_ocr_cache()
    hit = cache_get(
        db,
        kind="vision_ocr",
        cache_key=cache_key,
        ttl_seconds=cache_plan.transcription_ttl_seconds,
    )
    hit = pick(
        should_revalidate_ocr_blank(
            hit=pick(isinstance(hit, str), lambda: hit, lambda: None),
            requested_ttl_seconds=cache_plan.transcription_ttl_seconds,
            blank_trust_ttl_seconds=cache_plan.blank_trust_ttl_seconds,
        ),
        lambda: cache_get(db, kind="vision_ocr", cache_key=cache_key),
        lambda: hit,
    )
    return pick(
        isinstance(hit, str) and bool(hit),
        lambda: pick(hit == "BLANK", lambda: "", lambda: hit),
        lambda: _call_vision(db, doc, document_id, page_number, model_id, cache_key, cache_plan, cache_put),
    )


def _call_vision(db, doc, document_id, page_number, model_id, cache_key, cache_plan, cache_put) -> str:
    image_url = _page_image_url(db, doc, page_number)
    return pick(not image_url, lambda: "", lambda: _run_ocr(db, doc, document_id, page_number, model_id, cache_key, cache_plan, cache_put, image_url))


def _run_ocr(db, doc, document_id, page_number, model_id, cache_key, cache_plan, cache_put, image_url) -> str:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": _OCR_USER},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    try:
        raw = run_coro_in_worker(
            acomplete_chat(
                [
                    {"role": "system", "content": _OCR_SYSTEM},
                    {"role": "user", "content": content},
                ],
                db,
                model_id=model_id,
                require_vision=True,
                log_tag="vision_ocr",
                document_id=doc.id,
            )
        )
    except Exception:
        logger.warning(
            "vision OCR failed for doc %s page %s",
            document_id,
            page_number,
            exc_info=True,
        )
        return ""

    out = (raw or "").strip()
    blank = not out or out.upper() == "BLANK"
    value = pick(blank, lambda: "BLANK", lambda: out)
    cache_put(
        db,
        kind="vision_ocr",
        cache_key=cache_key,
        value=value,
        ttl_seconds=cache_plan.transcription_ttl_seconds,
    )
    return pick(blank, lambda: "", lambda: out)
