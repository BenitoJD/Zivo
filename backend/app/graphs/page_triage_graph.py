"""Page triage — agent estimates question budget and testable aspects."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections import OrderedDict
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.repositories.intel import update_activity
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.mcq_dedup import dedupe_aspects
from app.services.prompts import get_prompt
from app.services.question_budget import (
    Centrality,
    Mode,
    Unit,
    plan_page_budget,
)
from app.services.question_pool import (
    on_triage_completed,
    save_page_coverage,
)
from app.services.retrieval import fetch_chunks_for_page_range
from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens

logger = logging.getLogger(__name__)

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
    from app.models import Document
    from app.services.question_pool import (
        note_empty_page_triage,
        reset_empty_page_streak,
        should_skip_empty_page_vision,
    )
    from app.services.rag_window import pages_ready_for_document
    from app.services.vision import judge_page_has_content

    chunks = fetch_chunks_for_page_range(
        db,
        document_ids=[document_id],
        page_start=page_number,
        page_end=page_number,
    )
    page_text = "\n\n".join(c["text"] for c in chunks if c.get("text")).strip()

    # Guard the indexing race. Eager lookahead triage can reach a page BEFORE
    # ingest finishes. Defer (write no coverage) until the page is ready —
    # either chunks exist, or ingest marked it in meta.ingested_pages (empty scan).
    if not page_text:
        if page_number not in pages_ready_for_document(db, document_id):
            logger.info(
                "page triage deferred: page %s not ingested yet (doc %s)",
                page_number,
                document_id,
            )
            return {
                "question_budget": 0,
                "aspects": [],
                "rationale": "Deferred — page text not indexed yet.",
                "aspect_dedup": None,
                "content_type": None,
                "non_content": False,
                "deferred": True,
            }

        # Empty extractable text: one vision glance, then skip (cannot quiz without text).
        # Vision changes the ask: blank → quiet skip; usable → stop and ask human;
        # five blanks → stop and ask. After we asked, skip further vision calls.
        vision_usable = False
        rationale = "No extractable text on this page."
        doc = db.get(Document, document_id)
        if doc and should_skip_empty_page_vision(doc):
            rationale = "Skipped vision — already asked learner to reselect pages."
        else:
            verdict = judge_page_has_content(db, document_id, page_number)
            vision_usable = bool(verdict.get("usable"))
            rationale = str(verdict.get("rationale") or "").strip() or (
                "Page looks like it has content, but no extractable text."
                if vision_usable
                else "Vision glance: no useful study content."
            )
            note_empty_page_triage(db, document_id, vision_usable=vision_usable)

        from app.services.content_worthiness import evaluate_worthiness

        worth = evaluate_worthiness(
            page_text=page_text or "",
            empty=True,
            min_chars=40,
        )
        result = _non_content_result(
            rationale=f"{rationale} [worthiness:{worth.reason}]"
        )
        _persist_triage_coverage(
            db,
            document_id,
            page_number=page_number,
            result=result,
            activity_id=activity_id,
        )
        on_triage_completed(db, document_id, page=page_number, precompute=precompute)
        if activity_id:
            update_activity(
                db,
                uuid.UUID(str(activity_id)),
                status="succeeded",
                stats={
                    "question_budget": 0,
                    "aspects_count": 0,
                    "page_number": page_number,
                    "vision_usable": vision_usable,
                },
                finished=True,
            )
        db.commit()
        return result

    reset_empty_page_streak(db, document_id)

    # Newspaper editions: drop ads/junk/off-syllabus before triage so MCQs stay
    # UPSC/Group-1 signal-only. Combined verdict also closes soft entertainment.
    doc_for_signal = db.get(Document, document_id)
    is_newspaper = bool(doc_for_signal and (doc_for_signal.meta or {}).get("newspaper"))
    if is_newspaper:
        from app.services.newspaper_ad_filter import newspaper_page_verdict

        verdict, rationale = newspaper_page_verdict(page_text)
        if verdict != "cook":
            from app.services.content_worthiness import evaluate_worthiness

            worth = evaluate_worthiness(
                page_text=page_text,
                ad_likely=(verdict == "ad"),
                non_content=(verdict != "cook"),
            )
            # Newspaper cook plans Learn by default (docs/QUESTION_BUDGET_ENGINE.md §0).
            result = _non_content_result(
                rationale=(
                    f"Newspaper filter ({verdict}): {rationale}"
                    f" [worthiness:{worth.reason}]"
                ),
                mode="learn",
            )
            _persist_triage_coverage(
                db,
                document_id,
                page_number=page_number,
                result=result,
                activity_id=activity_id,
            )
            on_triage_completed(db, document_id, page=page_number, precompute=precompute)
            if activity_id:
                update_activity(
                    db,
                    uuid.UUID(str(activity_id)),
                    status="succeeded",
                    stats={
                        "question_budget": 0,
                        "aspects_count": 0,
                        "page_number": page_number,
                        "newspaper_filter": verdict,
                    },
                    finished=True,
                )
            db.commit()
            return result

    # Product rule: newspaper editions cook with Learn (m=1). Test re-plans at
    # serve time via learn-queue ?mode=test (see QUESTION_BUDGET_ENGINE.md §0).
    triage_mode: Mode = "learn"
    result = _triage_page(
        db, page_text=page_text, page_number=page_number, mode=triage_mode
    )

    content_type = result.get("content_type")
    if is_newspaper and not result.get("non_content"):
        from app.services.newspaper_ad_filter import NEWSPAPER_EXAM_CONTENT_TYPE

        content_type = NEWSPAPER_EXAM_CONTENT_TYPE

    if content_type is not None:
        result = {**result, "content_type": content_type}
    _persist_triage_coverage(
        db,
        document_id,
        page_number=page_number,
        result=result,
        activity_id=activity_id,
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


def _persist_triage_coverage(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    result: dict[str, Any],
    activity_id: str | None,
) -> None:
    """Write page_coverage including planner metadata (budget_version, confidence)."""
    save_page_coverage(
        db,
        document_id,
        page=page_number,
        question_budget=int(result.get("question_budget") or 0),
        aspects=list(result.get("aspects") or []),
        rationale=str(result.get("rationale") or ""),
        triage_activity_id=activity_id,
        aspect_dedup=result.get("aspect_dedup"),
        content_type=result.get("content_type"),
        non_content=bool(result.get("non_content")),
        programmable=bool(result.get("programmable")),
        budget_confidence=result.get("budget_confidence"),
        budget_mode=result.get("budget_mode"),
        budget_version=result.get("budget_version"),
        n_cov=result.get("n_cov"),
    )


def _parse_centrality(raw: Any) -> Centrality:
    value = str(raw or "central").strip().lower()
    if value in ("central", "support", "skip"):
        return value  # type: ignore[return-value]
    return "central"


def _units_from_aspects(aspects: list[dict[str, Any]]) -> list[Unit]:
    return [
        Unit(
            key=str(a.get("key") or f"aspect-{i + 1}"),
            centrality=_parse_centrality(a.get("centrality")),
        )
        for i, a in enumerate(aspects)
    ]


def _select_aspects_for_plan(
    aspects: list[dict[str, Any]], *, n_page: int
) -> list[dict[str, Any]]:
    """Prefer central units; keep enough aspects for coverage without exceeding plan."""
    cookable = [a for a in aspects if _parse_centrality(a.get("centrality")) != "skip"]
    centrals = [a for a in cookable if _parse_centrality(a.get("centrality")) == "central"]
    supports = [a for a in cookable if _parse_centrality(a.get("centrality")) == "support"]
    ordered = centrals + supports
    keep = max(n_page, len(centrals))
    return ordered[:keep] if keep else ordered


def _normalize_aspect(item: Any, index: int, page_number: int) -> dict[str, Any] | None:
    if isinstance(item, str) and item.strip():
        key = re.sub(r"[^a-z0-9]+", "-", item.strip().lower())[:48].strip("-") or f"aspect-{index}"
        return {
            "key": key,
            "label": item.strip(),
            "centrality": "central",
            "asked": False,
            "answered": False,
        }
    if isinstance(item, dict):
        label = (item.get("label") or item.get("name") or "").strip()
        if not label:
            return None
        key = (item.get("key") or "").strip()
        if not key:
            key = re.sub(r"[^a-z0-9]+", "-", label.lower())[:48].strip("-") or f"aspect-{index}"
        aspect: dict[str, Any] = {
            "key": key,
            "label": label,
            "centrality": _parse_centrality(item.get("centrality")),
            "asked": False,
            "answered": False,
        }
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
    *,
    content_type: str = "non_content",
    rationale: str = "",
    programmable: bool = False,
    mode: Mode = "learn",
) -> dict[str, Any]:
    """A deliberate, honest zero-question verdict (distinct from a parse failure)."""
    plan = plan_page_budget(None, mode=mode, non_content=True, confidence="high")
    return {
        "question_budget": 0,
        "aspects": [],
        "rationale": rationale or "No testable content on this page.",
        "aspect_dedup": None,
        "content_type": content_type or "non_content",
        "programmable": bool(programmable),
        "non_content": True,
        "budget_confidence": plan.confidence,
        "budget_mode": plan.mode,
        "budget_version": plan.budget_version,
        "n_cov": plan.n_cov,
    }


def _fallback_triage(page_text: str, page_number: int, *, mode: Mode = "learn") -> dict[str, Any]:
    # Density prior (~one idea / 120 words) with substantial paragraphs as labels —
    # never raw ``\\n\\n`` count. Planner owns N after units are built.
    _MIN_SUBSTANTIAL_WORDS = 12
    words = len(page_text.split()) if page_text else 0
    paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()] if page_text else []
    substantial = [p for p in paragraphs if len(p.split()) >= _MIN_SUBSTANTIAL_WORDS]
    word_estimate = words // 120
    if words > 0 and word_estimate == 0:
        word_estimate = 1
    if substantial:
        aspect_count = min(len(substantial), word_estimate)
    elif words > 0:
        aspect_count = word_estimate
    else:
        aspect_count = 0
    labels = substantial if substantial else paragraphs
    aspects = []
    for i, para in enumerate(labels[:aspect_count]):
        label = para[:120].replace("\n", " ")
        aspects.append(
            {
                "key": f"page-{page_number}-p{i + 1}",
                "label": label,
                "centrality": "central",
                "asked": False,
                "answered": False,
            }
        )
    # Some text but no usable labels — one consolidated aspect rather than zero.
    if not aspects and words > 0:
        aspects = [
            {
                "key": f"page-{page_number}-main",
                "label": "Main ideas on this page",
                "centrality": "central",
                "asked": False,
                "answered": False,
            }
        ]
    # No testable text at all → genuinely non-content. Must be marked so the page
    # completes (is_page_complete treats 0 generated + not non_content as "not done"
    # and would otherwise strand the learner here with nothing to answer).
    if not aspects:
        from app.services.content_worthiness import evaluate_worthiness

        worth = evaluate_worthiness(page_text=page_text or "", empty=(words == 0))
        return _non_content_result(
            rationale=(
                "Heuristic triage: no testable text on this page."
                f" [worthiness:{worth.reason}]"
            ),
            mode=mode,
        )
    return _finalize_triage(
        aspects=aspects,
        rationale="Heuristic triage from page length.",
        mode=mode,
        words=words,
        substantial_paragraphs=len(substantial),
        confidence="medium",
    )


def _finalize_triage(
    *,
    aspects: list[dict[str, Any]],
    rationale: str,
    mode: Mode = "learn",
    content_type: str | None = None,
    programmable: bool = False,
    words: int = 0,
    substantial_paragraphs: int = 0,
    confidence: Literal["high", "medium", "low"] | None = None,
) -> dict[str, Any]:
    """Dedup aspects, normalize KC keys, then let plan_page_budget own N."""
    from app.services.kc_coverage import normalize_key

    deduped, meta = dedupe_aspects(aspects)
    for aspect in deduped:
        aspect.setdefault("centrality", "central")
        aspect["key"] = normalize_key(str(aspect.get("key") or aspect.get("label") or ""))
    units = _units_from_aspects(deduped)
    plan = plan_page_budget(
        units if units else None,
        mode=mode,
        non_content=False,
        words=words,
        substantial_paragraphs=substantial_paragraphs,
        confidence=confidence,
    )
    if plan.n_page == 0:
        return _non_content_result(
            content_type=content_type or "non_content",
            rationale=(rationale or "Planner: no cookable units on this page.").strip(),
            programmable=programmable,
            mode=mode,
        )

    selected = _select_aspects_for_plan(deduped, n_page=plan.n_page)
    dedup_note = ""
    if meta["raw_count"] != meta["deduped_count"]:
        dedup_note = f" {meta['deduped_count']} unique aspects after dedup ({meta['raw_count']} raw)."
    return {
        "question_budget": plan.n_page,
        "aspects": selected,
        "rationale": (rationale + dedup_note).strip(),
        "aspect_dedup": meta,
        "content_type": content_type,
        "programmable": bool(programmable),
        "non_content": False,
        "budget_confidence": plan.confidence,
        "budget_mode": plan.mode,
        "budget_version": plan.budget_version,
        "n_cov": plan.n_cov,
        "kc_policy_version": "qb.kc.v1",
    }


def _triage_page(
    db: Session, *, page_text: str, page_number: int, mode: Mode = "learn"
) -> dict[str, Any]:
    from app.config import get_settings
    from app.services.chunk_map_cache import triage_cache_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    settings = get_settings()
    allow_zero = settings.allow_zero_questions
    use_llm = bool(settings.llm_page_triage)
    excerpt = truncate_to_tokens(page_text, _TRIAGE_PAGE_MAX_TOKENS) if page_text else ""

    # Separate cache namespaces so flipping llm_page_triage never serves a stale
    # LLM plan as a "heuristic" result (or the reverse).
    cache_kind = "page_triage" if use_llm else "page_triage_heuristic"
    # In-process LRU is shared; prefix the logical key via page_number namespace.
    cache_page_key = page_number if use_llm else -page_number

    cached = _triage_cache.get(page_text, cache_page_key)
    if cached is not None:
        return cached

    db_key = f"{cache_kind}:{triage_cache_key(page_text, page_number)}"
    db_hit = cache_get(db, kind=cache_kind, cache_key=db_key)
    if isinstance(db_hit, dict) and db_hit:
        _triage_cache.put(page_text, cache_page_key, db_hit)
        return dict(db_hit)

    def _store(result: dict[str, Any]) -> dict[str, Any]:
        _triage_cache.put(page_text, cache_page_key, result)
        try:
            cache_put(db, kind=cache_kind, cache_key=db_key, value=result)
        except Exception:
            logger.debug("page triage DB cache write failed", exc_info=True)
        return result

    # Empty / junk → honest zero when allowed (no LLM either way).
    if allow_zero and (not excerpt or _looks_like_junk(excerpt)):
        return _store(
            _non_content_result(
                rationale="Page has no coherent testable text.",
                mode=mode,
            )
        )

    # Jobs default: instant heuristic plan. LLM only when explicitly re-enabled.
    if not use_llm:
        return _store(_fallback_triage(page_text, page_number, mode=mode))

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
                programmable = bool(parsed.get("programmable"))
                aspects_raw = parsed.get("aspects") or []
                aspects: list[dict[str, Any]] = []
                for i, item in enumerate(aspects_raw):
                    norm = _normalize_aspect(item, i + 1, page_number)
                    if norm:
                        aspects.append(norm)
                rationale = (parsed.get("rationale") or "").strip()
                words = len(page_text.split()) if page_text else 0
                substantial = sum(
                    1
                    for p in (page_text.split("\n\n") if page_text else [])
                    if p.strip() and len(p.split()) >= 12
                )

                if allow_zero:
                    # Honest zero: model judged non-content / unusable / no units.
                    # N itself comes from plan_page_budget — never raw LLM yield.
                    if content_type == "non_content" or usable is False or not aspects:
                        return _store(
                            _non_content_result(
                                content_type=content_type or "non_content",
                                rationale=rationale,
                                programmable=programmable,
                                mode=mode,
                            )
                        )
                    return _store(
                        _finalize_triage(
                            aspects=aspects,
                            rationale=rationale,
                            mode=mode,
                            content_type=content_type,
                            programmable=programmable,
                            words=words,
                            substantial_paragraphs=substantial,
                            confidence="high",
                        )
                    )

                # Legacy path (flag off): still formula-owned N; no floor/ceiling
                # beyond the planner. Empty aspects → heuristic fallback.
                if not aspects:
                    return _store(_fallback_triage(page_text, page_number, mode=mode))
                return _store(
                    _finalize_triage(
                        aspects=aspects,
                        rationale=rationale,
                        mode=mode,
                        content_type=content_type,
                        programmable=programmable,
                        words=words,
                        substantial_paragraphs=substantial,
                        confidence="high",
                    )
                )
        except Exception:
            # LLM triage failed — degrade to heuristic budgeting. Log it: if this
            # fires for every page, the moat's first stage is silently down.
            logger.warning(
                "page triage LLM failed for page %s; using heuristic fallback",
                page_number,
                exc_info=True,
            )
    return _store(_fallback_triage(page_text, page_number, mode=mode))
