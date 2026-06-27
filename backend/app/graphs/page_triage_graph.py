"""Page triage — agent estimates question budget and testable aspects."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import OrderedDict
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.intel import update_activity
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.mcq_dedup import dedupe_aspects
from app.services.prompts import get_prompt
from app.services.question_pool import (
    ABSOLUTE_MAX_QUESTIONS_PER_PAGE,
    INITIAL_BATCH_SIZE,
    on_triage_completed,
    save_page_coverage,
)
from app.services.retrieval import fetch_chunks_for_page_range
from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens

_TRIAGE_PAGE_MAX_TOKENS = PAGE_INPUT_MAX_TOKENS


class _TriageCache:
    """In-process LRU cache of triage JSON keyed by page-text hash."""

    def __init__(self, max_entries: int = 128) -> None:
        self._store: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max = max_entries

    def _key(self, page_text: str, page_number: int) -> str:
        digest = hashlib.sha256(page_text.encode("utf-8", "ignore")).hexdigest()
        return f"{page_number}:{digest}"

    def get(self, page_text: str, page_number: int) -> dict[str, Any] | None:
        key = self._key(page_text, page_number)
        hit = self._store.get(key)
        if hit is None:
            return None
        self._store.move_to_end(key)
        return dict(hit)

    def put(self, page_text: str, page_number: int, result: dict[str, Any]) -> None:
        key = self._key(page_text, page_number)
        self._store[key] = dict(result)
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)


_triage_cache = _TriageCache()


def run_page_triage(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    activity_id: str | None = None,
    precompute: bool = False,
) -> dict[str, Any]:
    chunks = fetch_chunks_for_page_range(
        db,
        document_ids=[document_id],
        page_start=page_number,
        page_end=page_number,
    )
    page_text = "\n\n".join(c["text"] for c in chunks if c.get("text")).strip()
    result = _triage_page(db, page_text=page_text, page_number=page_number)

    save_page_coverage(
        db,
        document_id,
        page=page_number,
        question_budget=result["question_budget"],
        aspects=result["aspects"],
        rationale=result.get("rationale", ""),
        triage_activity_id=activity_id,
        aspect_dedup=result.get("aspect_dedup"),
        content_type=result.get("content_type"),
        non_content=bool(result.get("non_content")),
    )
    on_triage_completed(db, document_id, page=page_number, precompute=precompute)

    if activity_id:
        update_activity(
            db,
            uuid.UUID(str(activity_id)),
            status="succeeded",
            stats={
                "question_budget": result["question_budget"],
                "aspects_count": len(result["aspects"]),
                "page_number": page_number,
            },
            finished=True,
        )
    db.commit()
    return result


def _complete_chat_sync(
    db: Session, messages: list[dict], *, model_id: uuid.UUID | None = None
) -> str:
    try:
        return run_coro_in_worker(
            acomplete_chat(messages, db, log_tag="page_triage", model_id=model_id)
        )
    except Exception:
        # Fall back to the pool if a pinned model stalls/errors, so triage (and thus
        # generation) recovers instead of failing the whole job.
        if model_id is None:
            raise
        return run_coro_in_worker(
            acomplete_chat(messages, db, log_tag="page_triage", model_id=None)
        )


def _parse_triage_json(raw: str) -> dict[str, Any] | None:
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
        return json.loads(text_block)
    except json.JSONDecodeError:
        return None


def _normalize_aspect(item: Any, index: int, page_number: int) -> dict[str, Any] | None:
    if isinstance(item, str) and item.strip():
        key = re.sub(r"[^a-z0-9]+", "-", item.strip().lower())[:48].strip("-") or f"aspect-{index}"
        return {"key": key, "label": item.strip(), "asked": False, "answered": False}
    if isinstance(item, dict):
        label = (item.get("label") or item.get("name") or "").strip()
        if not label:
            return None
        key = (item.get("key") or "").strip()
        if not key:
            key = re.sub(r"[^a-z0-9]+", "-", label.lower())[:48].strip("-") or f"aspect-{index}"
        aspect: dict[str, Any] = {"key": key, "label": label, "asked": False, "answered": False}
        angle = (item.get("cognitive_angle") or "").strip()
        if angle:
            aspect["cognitive_angle"] = angle
        return aspect
    return None


def _looks_like_junk(text: str) -> bool:
    """Cheap pre-LLM gate: is this coherent language a learner could be quizzed on?

    Conservative by design — only flags material that is clearly unusable
    (empty, mostly non-letters like raw tables/OCR noise, or too few real words).
    Used only when zero questions are permitted; legacy behaviour never calls it.
    """
    t = (text or "").strip()
    if len(t) < 24:
        return True
    letters = sum(1 for c in t if c.isalpha())
    if letters / max(len(t), 1) < 0.45:
        return True
    words = re.findall(r"[^\W\d_]{2,}", t, re.UNICODE)
    return len(words) < 8


def _non_content_result(
    *, content_type: str = "non_content", rationale: str = ""
) -> dict[str, Any]:
    """A deliberate, honest zero-question verdict (distinct from a parse failure)."""
    return {
        "question_budget": 0,
        "aspects": [],
        "rationale": rationale or "No testable content on this page.",
        "aspect_dedup": None,
        "content_type": content_type or "non_content",
        "non_content": True,
    }


def _fallback_triage(page_text: str, page_number: int) -> dict[str, Any]:
    words = len(page_text.split()) if page_text else 0
    paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()] if page_text else []
    aspect_count = max(3, min(50, len(paragraphs) or max(3, words // 120)))
    budget = max(INITIAL_BATCH_SIZE, min(ABSOLUTE_MAX_QUESTIONS_PER_PAGE, aspect_count))
    aspects = []
    for i, para in enumerate(paragraphs[:budget]):
        label = para[:120].replace("\n", " ")
        aspects.append(
            {
                "key": f"page-{page_number}-p{i + 1}",
                "label": label,
                "asked": False,
                "answered": False,
            }
        )
    if not aspects:
        aspects = [
            {
                "key": f"page-{page_number}-main",
                "label": "Main ideas on this page",
                "asked": False,
                "answered": False,
            }
        ]
        budget = INITIAL_BATCH_SIZE
    return _finalize_triage(
        aspects=aspects,
        budget=budget,
        rationale="Heuristic triage from page length.",
    )


def _finalize_triage(
    *,
    aspects: list[dict[str, Any]],
    budget: int,
    rationale: str,
    content_type: str | None = None,
) -> dict[str, Any]:
    deduped, meta = dedupe_aspects(aspects)
    new_budget = min(budget, len(deduped)) if deduped else 0
    if new_budget == 0 and deduped:
        new_budget = len(deduped)
    dedup_note = ""
    if meta["raw_count"] != meta["deduped_count"]:
        dedup_note = f" {meta['deduped_count']} unique aspects after dedup ({meta['raw_count']} raw)."
    return {
        "question_budget": new_budget,
        "aspects": deduped[:new_budget] if new_budget else deduped,
        "rationale": (rationale + dedup_note).strip(),
        "aspect_dedup": meta,
        "content_type": content_type,
        "non_content": False,
    }


def _triage_page(db: Session, *, page_text: str, page_number: int) -> dict[str, Any]:
    cached = _triage_cache.get(page_text, page_number)
    if cached is not None:
        return cached

    from app.config import get_settings

    allow_zero = get_settings().allow_zero_questions

    excerpt = truncate_to_tokens(page_text, _TRIAGE_PAGE_MAX_TOKENS) if page_text else ""

    # Open-world pre-LLM gate: when zero is permitted, short-circuit obviously
    # non-content material (empty/junk) without spending an LLM call. Legacy
    # behaviour (flag off) never reaches this and keeps the >=5 floor below.
    if allow_zero and (not excerpt or _looks_like_junk(excerpt)):
        result = _non_content_result(rationale="Page has no coherent testable text.")
        _triage_cache.put(page_text, page_number, result)
        return result

    if excerpt:
        try:
            # Pin triage to the default model rather than round-robining the pool,
            # so triage wall-clock isn't gated by the slowest pool model.
            from app.services.llm_registry import default_chat_model_id

            model_id = default_chat_model_id(db)
            system = get_prompt(db, "page_triage_system")
            instructions = get_prompt(
                db,
                "page_triage_format",
                page_text="(the page text provided above)",
            )
            # Stable-prefix ordering for provider prompt caching: system prompt
            # + page text lead (byte-identical across pages only by page, but
            # stable across retries/lookahead on the SAME page), with the
            # variable JSON instructions in the trailing message.
            raw = _complete_chat_sync(
                db,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Page text:\n{excerpt}"},
                    {"role": "user", "content": instructions},
                ],
                model_id=model_id,
            )
            parsed = _parse_triage_json(raw)
            if parsed:
                content_type = (str(parsed.get("content_type") or "").strip().lower() or None)
                usable = parsed.get("usable")
                raw_yield = parsed.get("testable_yield")
                if raw_yield is None:
                    raw_yield = parsed.get("question_budget")
                aspects_raw = parsed.get("aspects") or []
                aspects: list[dict[str, Any]] = []
                for i, item in enumerate(aspects_raw):
                    norm = _normalize_aspect(item, i + 1, page_number)
                    if norm:
                        aspects.append(norm)
                rationale = (parsed.get("rationale") or "").strip()

                if allow_zero:
                    # Honest zero: the model judged non-content, unusable, or had
                    # nothing worth asking. This is a valid high-quality verdict.
                    budget = int(raw_yield or 0)
                    if content_type == "non_content" or usable is False or budget <= 0 or not aspects:
                        result = _non_content_result(
                            content_type=content_type or "non_content",
                            rationale=rationale,
                        )
                        _triage_cache.put(page_text, page_number, result)
                        return result
                    budget = max(1, min(ABSOLUTE_MAX_QUESTIONS_PER_PAGE, budget))
                    if budget < len(aspects):
                        budget = min(ABSOLUTE_MAX_QUESTIONS_PER_PAGE, len(aspects))
                    result = _finalize_triage(
                        aspects=aspects[:budget],
                        budget=budget,
                        rationale=rationale,
                        content_type=content_type,
                    )
                    _triage_cache.put(page_text, page_number, result)
                    return result

                # Legacy path (flag off): keep the >=5 floor; content_type is
                # captured for free but never forces a zero.
                budget = int(raw_yield or INITIAL_BATCH_SIZE)
                budget = max(INITIAL_BATCH_SIZE, min(ABSOLUTE_MAX_QUESTIONS_PER_PAGE, budget))
                if not aspects:
                    return _fallback_triage(page_text, page_number)
                if budget < len(aspects):
                    budget = min(ABSOLUTE_MAX_QUESTIONS_PER_PAGE, len(aspects))
                result = _finalize_triage(
                    aspects=aspects[:budget],
                    budget=budget,
                    rationale=rationale,
                    content_type=content_type,
                )
                _triage_cache.put(page_text, page_number, result)
                return result
        except Exception:
            pass
    result = _fallback_triage(page_text, page_number)
    _triage_cache.put(page_text, page_number, result)
    return result
