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
from app.services.aspect_discovery import dedupe_aspects, parse_centrality, pick_for_plan
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
        # Vision plumbing returns usable; Content Worthiness owns the gate reason.
        # blank → quiet skip; usable → stop and ask human; five blanks → stop and ask.
        from app.services.content_worthiness import (
            evaluate_vision_glance,
            evaluate_worthiness,
        )

        vision_usable = False
        rationale = "No extractable text on this page."
        worth = evaluate_worthiness(
            page_text=page_text or "",
            empty=True,
            min_chars=40,
        )
        doc = db.get(Document, document_id)
        if doc and should_skip_empty_page_vision(doc):
            rationale = "Skipped vision — already asked learner to reselect pages."
        else:
            glance = judge_page_has_content(db, document_id, page_number)
            vision_usable = bool(glance.get("usable"))
            worth = evaluate_vision_glance(
                usable=vision_usable,
                rationale=str(glance.get("rationale") or ""),
            )
            rationale = worth.details or rationale
            note_empty_page_triage(db, document_id, vision_usable=vision_usable)

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

    # Newspaper editions: Worthiness Engine owns ad/junk/off-syllabus skip.
    doc_for_signal = db.get(Document, document_id)
    is_newspaper = bool(doc_for_signal and (doc_for_signal.meta or {}).get("newspaper"))
    if is_newspaper:
        from app.services.content_worthiness import evaluate_worthiness

        worth = evaluate_worthiness(page_text=page_text, newspaper=True, db=db)
        if not worth.worthy:
            # Newspaper cook plans Learn by default (docs/QUESTION_BUDGET_ENGINE.md §0).
            result = _non_content_result(
                rationale=(
                    f"Newspaper filter ({worth.reason}): {worth.details or worth.reason}"
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
                        "newspaper_filter": worth.reason,
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
        aspects_raw = result.get("aspects") or []
        units = _units_from_aspects(aspects_raw)
        conf_raw = result.get("budget_confidence")
        conf: Literal["high", "medium", "low"] | None = None
        if conf_raw in ("high", "medium", "low"):
            conf = conf_raw
        test_plan = plan_page_budget(
            units if units else None,
            mode="test",
            non_content=False,
            words=len(page_text.split()) if page_text else 0,
            substantial_paragraphs=len(
                [p for p in page_text.split("\n\n") if p.strip()] if page_text else []
            ),
            confidence=conf,
        )
        test_selected = _select_aspects_for_plan(aspects_raw, n_page=test_plan.n_page)
        result = {
            **result,
            "test_question_budget": test_plan.n_page,
            "test_aspects": test_selected,
        }

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
        debuggable=bool(result.get("debuggable") or result.get("programmable")),
        budget_confidence=result.get("budget_confidence"),
        budget_mode=result.get("budget_mode"),
        budget_version=result.get("budget_version"),
        n_cov=result.get("n_cov"),
        test_question_budget=result.get("test_question_budget"),
        test_aspects=result.get("test_aspects"),
    )


def _parse_centrality(raw: Any) -> Centrality:
    return parse_centrality(raw)


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
    """Prefer central units - Aspect Discovery Engine owns the pick."""
    return list(pick_for_plan(aspects, n_page=n_page).aspects)


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
    """Compat shim → Content Worthiness Engine (tests / legacy imports)."""
    from app.services.content_worthiness import looks_like_junk

    return looks_like_junk(text)


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
    # Density prior via Aspect Discovery; planner owns N after units are built.
    from app.services.aspect_discovery import (
        FALLBACK_MIN_SUBSTANTIAL_WORDS,
        heuristic_fallback_aspects,
    )

    pick = heuristic_fallback_aspects(page_text, page_number)
    aspects = list(pick.aspects)
    words = len(page_text.split()) if page_text else 0
    paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()] if page_text else []
    substantial = [
        p for p in paragraphs if len(p.split()) >= FALLBACK_MIN_SUBSTANTIAL_WORDS
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
    """Dedup aspects, normalize via KC engine, then let plan_page_budget own N."""
    from app.services.kc_coverage import normalize_aspects, normalize_key

    dedupe_verdict = dedupe_aspects(aspects)
    deduped = list(dedupe_verdict.aspects)
    meta = {
        "raw_count": dedupe_verdict.raw_count,
        "deduped_count": dedupe_verdict.deduped_count,
        "merged_keys": list(dedupe_verdict.merged_keys),
    }
    for aspect in deduped:
        # Aspect Discovery owns centrality tokens (incl. peripheral→support, skip≠central).
        cent = parse_centrality(aspect.get("centrality"))
        aspect["centrality"] = cent
        aspect["central"] = cent == "central"
        if cent == "support":
            aspect["peripheral"] = True
    plan_aspects = normalize_aspects(deduped)
    # Merge engine keys back onto triage aspect dicts (preserve angles, etc.).
    unused = list(deduped)
    normalized_dicts: list[dict[str, Any]] = []
    for a in plan_aspects.aspects:
        matched_idx = None
        for i, d in enumerate(unused):
            raw_key = str(d.get("key") or d.get("label") or "")
            if normalize_key(raw_key) == a.key or str(d.get("label") or "") == a.label:
                matched_idx = i
                break
        base = dict(unused.pop(matched_idx)) if matched_idx is not None else {}
        base["key"] = a.key
        base["label"] = a.label or base.get("label") or a.key
        orig_cent = parse_centrality(base.get("centrality"))
        if orig_cent == "skip":
            # KC only has central bool; keep skip string for pick_for_plan / budget weights.
            base["centrality"] = "skip"
            base["central"] = False
        else:
            base["centrality"] = "central" if a.central else "support"
            base["central"] = a.central
        normalized_dicts.append(base)
    deduped = normalized_dicts
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

    # Empty / junk → Worthiness Engine owns honest zero (no parallel if-ladder).
    if allow_zero:
        from app.services.content_worthiness import evaluate_worthiness

        worth = evaluate_worthiness(
            page_text=excerpt or "",
            empty=not bool(excerpt),
            check_junk=True,
            min_chars=24,
        )
        if not worth.worthy:
            return _store(
                _non_content_result(
                    rationale=(
                        "Page has no coherent testable text."
                        f" [worthiness:{worth.reason}]"
                    ),
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
