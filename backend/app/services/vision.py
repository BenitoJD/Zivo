"""Multimodal chat helpers for image documents and empty-page vision glance."""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import pick
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

    def _from_cache() -> str:
        return cached

    def _load() -> str | None:
        def _too_big_meta() -> None:
            logger.warning("image document %s exceeds size cap (%s bytes)", doc.id, doc.size_bytes)
            return None

        def _fetch() -> str | None:
            data = fetch_object(doc.storage_key)

            def _too_big_data() -> None:
                logger.warning(
                    "fetched image for document %s exceeds cap (%s > %s bytes)",
                    doc.id,
                    len(data),
                    MAX_IMAGE_BYTES,
                )
                return None

            def _ok() -> str:
                encoded = base64.standard_b64encode(data).decode("ascii")
                url = f"data:{doc.content_type};base64,{encoded}"
                meta[_META_IMAGE_DATA_URL] = url
                doc.meta = meta
                pick(db is not None, lambda: (db.add(doc), db.commit()), lambda: None)
                return url

            return pick(len(data) > MAX_IMAGE_BYTES, _too_big_data, _ok)

        return pick((doc.size_bytes or 0) > MAX_IMAGE_BYTES, _too_big_meta, _fetch)

    return pick(isinstance(cached, str) and cached.startswith("data:"), _from_cache, _load)


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
    _ = (current_page, page_start, page_end)

    def _plain() -> str:
        return text

    def _with_image() -> str | list[dict]:
        image_url = document_image_data_url(doc, db=db)
        return pick(
            not image_url,
            _plain,
            lambda: [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        )

    return pick(not include_image or not is_image_document(doc), _plain, _with_image)


def _page_image_data_url(doc: Document, page_number: int) -> str | None:
    """Build a data URL for one PDF page (or the whole image document)."""

    def _from_image() -> str | None:
        return document_image_data_url(doc)

    def _from_pdf() -> str | None:
        raw = fetch_object(doc.storage_key)
        ct = (doc.content_type or "").lower()

        def _none() -> None:
            return None

        def _render() -> str | None:
            from app.services.parse import render_pdf_page_png

            png = render_pdf_page_png(raw, page_number, max_bytes=MAX_IMAGE_BYTES)

            def _encode() -> str:
                encoded = base64.standard_b64encode(png).decode("ascii")
                return f"data:image/png;base64,{encoded}"

            return pick(not png, _none, _encode)

        return pick("pdf" not in ct and raw[:4] != b"%PDF", _none, _render)

    return pick(is_image_document(doc), _from_image, _from_pdf)


def _parse_usable_json(raw: str) -> dict[str, Any] | None:
    def _none() -> None:
        return None

    def _parse() -> dict[str, Any] | None:
        text_block = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_block, re.DOTALL)

        def _from_fence() -> str:
            return fence.group(1)

        def _from_braces() -> str | None:
            start = text_block.find("{")
            end = text_block.rfind("}")
            return pick(start >= 0 and end > start, lambda: text_block[start : end + 1], _none)

        extracted = pick(bool(fence), _from_fence, _from_braces)

        def _load() -> dict[str, Any] | None:
            try:
                parsed = json.loads(extracted)
            except json.JSONDecodeError:
                return None
            return pick(isinstance(parsed, dict), lambda: parsed, _none)

        return pick(extracted is None, _none, _load)

    return pick(not raw or not raw.strip(), _none, _parse)


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

    def _missing() -> dict[str, Any]:
        return {"usable": False, "rationale": "Document missing."}

    def _run() -> dict[str, Any]:
        cache_key = content_hash_key(
            "page_vision_judge",
            doc.storage_key or str(doc.id),
            int(page_number),
        )
        hit = cache_get(db, kind="page_vision_judge", cache_key=cache_key)

        def _cached() -> dict[str, Any]:
            return {"usable": bool(hit.get("usable")), "rationale": str(hit.get("rationale") or "")}

        def _compute() -> dict[str, Any]:
            image_url = _page_image_data_url(doc, int(page_number))

            def _no_image() -> dict[str, Any]:
                verdict = {"usable": False, "rationale": "Could not render page for vision glance."}
                cache_put(db, kind="page_vision_judge", cache_key=cache_key, value=verdict)
                return verdict

            def _vision() -> dict[str, Any]:
                try:
                    raw = run_coro_in_worker(
                        acomplete_chat(
                            [
                                {"role": "system", "content": _PAGE_VISION_JUDGE_SYSTEM},
                                {"role": "user", "content": [
                                    {"type": "text", "text": _PAGE_VISION_JUDGE_USER},
                                    {"type": "image_url", "image_url": {"url": image_url}},
                                ]},
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
                verdict = pick(
                    not parsed,
                    lambda: {"usable": False, "rationale": "Vision glance returned no usable judgment."},
                    lambda: {
                        "usable": bool(parsed.get("usable")),
                        "rationale": str(parsed.get("rationale") or "").strip(),
                    },
                )
                cache_put(db, kind="page_vision_judge", cache_key=cache_key, value=verdict)
                return verdict

            return pick(not image_url, _no_image, _vision)

        return pick(isinstance(hit, dict) and "usable" in hit, _cached, _compute)

    return pick(not doc, _missing, _run)
