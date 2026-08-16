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

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.repositories.intel import update_activity
from app.services.llm_router import acomplete_chat
from app.services.llm_route import evaluate_llm_route
from app.services.llm_sync import run_coro_in_worker
from app.services.aspect_discovery import (
    dedupe_aspects,
    parse_centrality,
    pick_for_plan,
    substantial_paragraphs,
)
from app.services.presence import evaluate_presence
from app.services.prompts import get_prompt
from app.services.question_budget import (
    Centrality,
    Mode,
    Unit,
    plan_newspaper_test_triage,
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

_ASPECT_SHAPE_RULES = (
    Rule(when=(Pred("is_str", "truthy"), Pred("has_text", "truthy")), action="from_str"),
    Rule(when=(Pred("is_dict", "truthy"),), action="from_dict"),
    Rule(when=(), action="drop"),
)

_CONFIDENCE = frozenset({"high", "medium", "low"})


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

        def _touch() -> dict[str, Any]:
            self._store.move_to_end(key)
            return dict(hit)

        return pick(hit is None, lambda: None, _touch)

    def put(self, page_text: str, page_number: int, result: dict[str, Any]) -> None:
        key = self._key(page_text, page_number)
        self._store[key] = dict(result)
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)


_triage_cache = _TriageCache()


def _maybe_activity(
    db: Session,
    activity_id: str | None,
    stats: dict[str, Any],
) -> None:
    pick(
        bool(activity_id),
        lambda: update_activity(
            db,
            uuid.UUID(str(activity_id)),
            status="succeeded",
            stats=stats,
            finished=True,
        ),
        lambda: None,
    )


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
    page_text = "\n\n".join(
        c["text"] for c in filter(lambda c: c.get("text"), chunks)
    ).strip()

    def _finish(result: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
        _persist_triage_coverage(
            db,
            document_id,
            page_number=page_number,
            result=result,
            activity_id=activity_id,
        )
        on_triage_completed(db, document_id, page=page_number, precompute=precompute)
        _maybe_activity(db, activity_id, stats)
        db.commit()
        return result

    def _empty_page() -> dict[str, Any]:
        def _deferred() -> dict[str, Any]:
            logger.info(
                "page triage deferred: page %s not ingested yet (doc %s)",
                page_number,
                document_id,
            )
            return {
                "question_budget": 0,
                "aspects": [],
                "rationale": "Deferred: page text not indexed yet.",
                "aspect_dedup": None,
                "content_type": None,
                "non_content": False,
                "deferred": True,
            }

        def _vision_path() -> dict[str, Any]:
            from app.services.content_worthiness import (
                evaluate_vision_glance,
                evaluate_worthiness,
                plan_worthiness_probe,
            )

            box: dict[str, Any] = {
                "vision_usable": False,
                "rationale": "No extractable text on this page.",
            }
            probe = plan_worthiness_probe("empty_page")
            worth = evaluate_worthiness(
                page_text=page_text or "",
                empty=probe.empty,
                check_junk=probe.check_junk,
                min_chars=probe.min_chars,
            )
            doc = db.get(Document, document_id)

            def _skip_vision() -> None:
                box["rationale"] = "Skipped vision: already asked learner to reselect pages."

            def _glance() -> None:
                glance = judge_page_has_content(db, document_id, page_number)
                box["vision_usable"] = bool(glance.get("usable"))
                glanced = evaluate_vision_glance(
                    usable=box["vision_usable"],
                    rationale=str(glance.get("rationale") or ""),
                )
                box["worth"] = glanced
                box["rationale"] = glanced.details or box["rationale"]
                note_empty_page_triage(db, document_id, vision_usable=box["vision_usable"])

            box["worth"] = worth
            pick(
                bool(doc) and should_skip_empty_page_vision(doc),
                _skip_vision,
                _glance,
            )
            result = _non_content_result(
                rationale=f"{box['rationale']} [worthiness:{box['worth'].reason}]"
            )
            return _finish(
                result,
                {
                    "question_budget": 0,
                    "aspects_count": 0,
                    "page_number": page_number,
                    "vision_usable": box["vision_usable"],
                },
            )

        return pick(
            page_number not in pages_ready_for_document(db, document_id),
            _deferred,
            _vision_path,
        )

    def _content_page() -> dict[str, Any]:
        reset_empty_page_streak(db, document_id)
        doc_for_signal = db.get(Document, document_id)
        is_newspaper = bool(doc_for_signal and (doc_for_signal.meta or {}).get("newspaper"))

        def _newspaper_gate() -> dict[str, Any]:
            from app.services.content_worthiness import evaluate_worthiness

            worth = evaluate_worthiness(page_text=page_text, newspaper=True, db=db)

            def _skip_paper() -> dict[str, Any]:
                result = _non_content_result(
                    rationale=(
                        f"Newspaper filter ({worth.reason}): {worth.details or worth.reason}"
                        f" [worthiness:{worth.reason}]"
                    ),
                    mode="learn",
                )
                return _finish(
                    result,
                    {
                        "question_budget": 0,
                        "aspects_count": 0,
                        "page_number": page_number,
                        "newspaper_filter": worth.reason,
                    },
                )

            return pick(not worth.worthy, _skip_paper, _finish_triage)

        def _finish_triage() -> dict[str, Any]:
            triage_mode: Mode = "learn"
            result = _triage_page(
                db, page_text=page_text, page_number=page_number, mode=triage_mode
            )
            content_type = result.get("content_type")
            conf_raw = result.get("budget_confidence")
            aspects_raw = result.get("aspects") or []
            test_overlay = plan_newspaper_test_triage(
                is_newspaper=is_newspaper,
                non_content=bool(result.get("non_content")),
                aspects=aspects_raw,
                units=pick(
                    is_newspaper,
                    lambda: _units_from_aspects(aspects_raw),
                    lambda: None,
                ),
                words=pick(bool(page_text), lambda: len(page_text.split()), lambda: 0),
                substantial_paragraphs=len(substantial_paragraphs(page_text)),
                confidence=pick(conf_raw in _CONFIDENCE, lambda: conf_raw, lambda: None),
            )

            def _apply_overlay() -> None:
                nonlocal content_type, result
                content_type = test_overlay.content_type
                result = {
                    **result,
                    "test_question_budget": test_overlay.test_question_budget,
                    "test_aspects": list(test_overlay.test_aspects),
                }

            pick(test_overlay.apply, _apply_overlay, lambda: None)

            def _stamp_type() -> None:
                nonlocal result
                result = {**result, "content_type": content_type}

            pick(content_type is not None, _stamp_type, lambda: None)
            return _finish(
                result,
                {
                    "question_budget": result["question_budget"],
                    "aspects_count": len(result["aspects"]),
                    "page_number": page_number,
                },
            )

        return pick(is_newspaper, _newspaper_gate, _finish_triage)

    return apply(
        evaluate_presence(page_text).action,
        {"ok": _content_page, "empty": _empty_page, "missing": _empty_page},
    )


def _complete_chat_sync(
    db: Session, messages: list[dict], *, model_id: uuid.UUID | None = None
) -> str:
    try:
        return run_coro_in_worker(
            acomplete_chat(messages, db, log_tag="page_triage", model_id=model_id)
        )
    except Exception as err:
        caught = err

        def _reraise() -> str:
            raise caught

        return apply(
            evaluate_llm_route(
                has_primary=model_id is not None,
                has_fallback=model_id is not None,
                primary_failed=True,
            ).action,
            {
                "use_primary": _reraise,
                "use_fallback": lambda: run_coro_in_worker(
                    acomplete_chat(messages, db, log_tag="page_triage", model_id=None)
                ),
                "skip": _reraise,
            },
        )


def _parse_triage_json(raw: str) -> dict[str, Any] | None:
    def _none() -> None:
        return None

    def _loads(block: str) -> dict[str, Any] | None:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            return None

    def _parse() -> dict[str, Any] | None:
        text_block = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_block, re.DOTALL)

        def _from_fence() -> dict[str, Any] | None:
            return _loads(fence.group(1))

        def _from_braces() -> dict[str, Any] | None:
            start = text_block.find("{")
            end = text_block.rfind("}")
            return pick(
                start >= 0 and end > start,
                lambda: _loads(text_block[start : end + 1]),
                _none,
            )

        return pick(bool(fence), _from_fence, _from_braces)

    return apply(
        evaluate_presence((raw or "").strip()).action,
        {"ok": _parse, "empty": _none, "missing": _none},
    )


def _persist_triage_coverage(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    result: dict[str, Any],
    activity_id: str | None,
) -> None:
    """Write page_coverage including planner metadata (budget_version, confidence)."""
    from app.services.session_design import alias_debuggable

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
        debuggable=alias_debuggable(
            programmable=bool(result.get("programmable")),
            debuggable=pick(
                "debuggable" in result,
                lambda: result.get("debuggable"),
                lambda: None,
            ),
        ),
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
    """Prefer central units: Aspect Discovery Engine owns the pick."""
    return list(pick_for_plan(aspects, n_page=n_page).aspects)


def _normalize_aspect(item: Any, index: int, page_number: int) -> dict[str, Any] | None:
    def _from_str() -> dict[str, Any]:
        key = re.sub(r"[^a-z0-9]+", "-", item.strip().lower())[:48].strip("-") or f"aspect-{index}"
        return {
            "key": key,
            "label": item.strip(),
            "centrality": "central",
            "asked": False,
            "answered": False,
        }

    def _from_dict() -> dict[str, Any] | None:
        label = (item.get("label") or item.get("name") or "").strip()

        def _built() -> dict[str, Any]:
            key = (item.get("key") or "").strip()
            key = pick(
                not key,
                lambda: re.sub(r"[^a-z0-9]+", "-", label.lower())[:48].strip("-")
                or f"aspect-{index}",
                lambda: key,
            )
            aspect: dict[str, Any] = {
                "key": key,
                "label": label,
                "centrality": _parse_centrality(item.get("centrality")),
                "asked": False,
                "answered": False,
            }
            angle = (item.get("cognitive_angle") or "").strip()
            pick(bool(angle), lambda: aspect.__setitem__("cognitive_angle", angle), lambda: None)
            return aspect

        return apply(
            evaluate_presence(label).action,
            {"ok": _built, "empty": lambda: None, "missing": lambda: None},
        )

    hit = first_match(
        _ASPECT_SHAPE_RULES,
        {
            "is_str": isinstance(item, str),
            "has_text": isinstance(item, str) and bool(item.strip()),
            "is_dict": isinstance(item, dict),
        },
    )
    return apply(
        hit.action,
        {"from_str": _from_str, "from_dict": _from_dict, "drop": lambda: None},
    )


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
    from app.services.aspect_discovery import heuristic_fallback_aspects

    pick_v = heuristic_fallback_aspects(page_text, page_number)
    aspects = list(pick_v.aspects)
    words = pick(bool(page_text), lambda: len(page_text.split()), lambda: 0)
    substantial = substantial_paragraphs(page_text)

    def _zero() -> dict[str, Any]:
        from app.services.content_worthiness import evaluate_worthiness

        worth = evaluate_worthiness(page_text=page_text or "", empty=(words == 0))
        return _non_content_result(
            rationale=(
                "Heuristic triage: no testable text on this page."
                f" [worthiness:{worth.reason}]"
            ),
            mode=mode,
        )

    def _plan() -> dict[str, Any]:
        return _finalize_triage(
            aspects=aspects,
            rationale="Heuristic triage from page length.",
            mode=mode,
            words=words,
            substantial_paragraphs=len(substantial),
            confidence="medium",
        )

    return apply(
        evaluate_presence(aspects).action,
        {"ok": _plan, "empty": _zero, "missing": _zero},
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
    from app.services.kc_coverage import normalize_aspects, normalize_key, stamp_aspect_flags, stamp_kc_centrality

    dedupe_verdict = dedupe_aspects(aspects)
    deduped = list(dedupe_verdict.aspects)
    meta = {
        "raw_count": dedupe_verdict.raw_count,
        "deduped_count": dedupe_verdict.deduped_count,
        "merged_keys": list(dedupe_verdict.merged_keys),
    }
    for aspect in deduped:
        cent = parse_centrality(aspect.get("centrality"))
        aspect["centrality"] = cent
        central, peripheral = stamp_aspect_flags(cent)
        aspect["central"] = central
        pick(peripheral, lambda a=aspect: a.__setitem__("peripheral", True), lambda: None)
    plan_aspects = normalize_aspects(deduped)
    unused = list(deduped)
    normalized_dicts: list[dict[str, Any]] = []
    for a in plan_aspects.aspects:
        matched_idx = next(
            filter(
                lambda i: normalize_key(str(unused[i].get("key") or unused[i].get("label") or ""))
                == a.key
                or str(unused[i].get("label") or "") == a.label,
                range(len(unused)),
            ),
            None,
        )
        base = pick(
            matched_idx is not None,
            lambda idx=matched_idx: dict(unused.pop(idx)),
            lambda: {},
        )
        base["key"] = a.key
        base["label"] = a.label or base.get("label") or a.key
        orig_cent = parse_centrality(base.get("centrality"))
        centrality, central = stamp_kc_centrality(
            orig_centrality=orig_cent, central=a.central
        )
        base["centrality"] = centrality
        base["central"] = central
        normalized_dicts.append(base)
    deduped = normalized_dicts
    units = _units_from_aspects(deduped)
    plan = plan_page_budget(
        units or None,
        mode=mode,
        non_content=False,
        words=words,
        substantial_paragraphs=substantial_paragraphs,
        confidence=confidence,
    )

    def _zero_plan() -> dict[str, Any]:
        return _non_content_result(
            content_type=content_type or "non_content",
            rationale=(rationale or "Planner: no cookable units on this page.").strip(),
            programmable=programmable,
            mode=mode,
        )

    def _keep_plan() -> dict[str, Any]:
        selected = _select_aspects_for_plan(deduped, n_page=plan.n_page)
        dedup_note = pick(
            meta["raw_count"] != meta["deduped_count"],
            lambda: (
                f" {meta['deduped_count']} unique aspects after dedup "
                f"({meta['raw_count']} raw)."
            ),
            lambda: "",
        )
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

    return pick(plan.n_page == 0, _zero_plan, _keep_plan)


def _triage_page(
    db: Session, *, page_text: str, page_number: int, mode: Mode = "learn"
) -> dict[str, Any]:
    from app.config import get_settings
    from app.services.chunk_map_cache import triage_cache_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    settings = get_settings()
    allow_zero = settings.allow_zero_questions
    use_llm = bool(settings.llm_page_triage)
    excerpt = pick(
        bool(page_text),
        lambda: truncate_to_tokens(page_text, _TRIAGE_PAGE_MAX_TOKENS),
        lambda: "",
    )
    cache_kind = choose(use_llm, "page_triage", "page_triage_heuristic")
    cache_page_key = choose(use_llm, page_number, -page_number)

    cached = _triage_cache.get(page_text, cache_page_key)

    def _after_mem() -> dict[str, Any]:
        db_key = f"{cache_kind}:{triage_cache_key(page_text, page_number)}"
        db_hit = cache_get(db, kind=cache_kind, cache_key=db_key)

        def _from_db() -> dict[str, Any]:
            _triage_cache.put(page_text, cache_page_key, db_hit)
            return dict(db_hit)

        def _compute() -> dict[str, Any]:
            def _store(result: dict[str, Any]) -> dict[str, Any]:
                _triage_cache.put(page_text, cache_page_key, result)
                try:
                    cache_put(db, kind=cache_kind, cache_key=db_key, value=result)
                except Exception:
                    logger.debug("page triage DB cache write failed", exc_info=True)
                return result

            def _heuristic() -> dict[str, Any]:
                return _store(_fallback_triage(page_text, page_number, mode=mode))

            def _llm_path() -> dict[str, Any]:
                def _try_llm() -> dict[str, Any]:
                    try:
                        from app.services.llm_registry import default_chat_model_id

                        model_id = default_chat_model_id(db)
                        system = get_prompt(db, "page_triage_system")
                        instructions = get_prompt(
                            db,
                            "page_triage_format",
                            page_text="(the page text provided above)",
                        )
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

                        def _from_parsed() -> dict[str, Any]:
                            content_type = (
                                str(parsed.get("content_type") or "").strip().lower() or None
                            )
                            usable = parsed.get("usable")
                            programmable = bool(parsed.get("programmable"))
                            aspects_raw = parsed.get("aspects") or []
                            aspects = list(
                                filter(
                                    None,
                                    (
                                        _normalize_aspect(item, i + 1, page_number)
                                        for i, item in enumerate(aspects_raw)
                                    ),
                                )
                            )
                            rationale = (parsed.get("rationale") or "").strip()
                            words = pick(
                                bool(page_text),
                                lambda: len(page_text.split()),
                                lambda: 0,
                            )
                            substantial = len(substantial_paragraphs(page_text))

                            def _final_high() -> dict[str, Any]:
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

                            def _zero_path() -> dict[str, Any]:
                                from app.services.content_worthiness import (
                                    evaluate_llm_triage_units,
                                )

                                zero = evaluate_llm_triage_units(
                                    content_type=content_type,
                                    usable=usable,
                                    aspect_count=len(aspects),
                                )
                                return pick(
                                    zero.zero_question,
                                    lambda: _store(
                                        _non_content_result(
                                            content_type=content_type or "non_content",
                                            rationale=rationale,
                                            programmable=programmable,
                                            mode=mode,
                                        )
                                    ),
                                    _final_high,
                                )

                            def _legacy() -> dict[str, Any]:
                                from app.services.content_worthiness import (
                                    should_heuristic_fallback_empty_aspects,
                                )

                                return pick(
                                    should_heuristic_fallback_empty_aspects(
                                        allow_zero=allow_zero, aspect_count=len(aspects)
                                    ),
                                    _heuristic,
                                    _final_high,
                                )

                            return pick(allow_zero, _zero_path, _legacy)

                        return pick(bool(parsed), _from_parsed, _heuristic)
                    except Exception:
                        logger.warning(
                            "page triage LLM failed for page %s; using heuristic fallback",
                            page_number,
                            exc_info=True,
                        )
                        return _heuristic()

                return pick(bool(excerpt), _try_llm, _heuristic)

            def _after_worth() -> dict[str, Any]:
                return pick(not use_llm, _heuristic, _llm_path)

            def _maybe_zero() -> dict[str, Any]:
                from app.services.content_worthiness import (
                    evaluate_worthiness,
                    plan_worthiness_probe,
                )

                probe = plan_worthiness_probe("pre_llm", has_text=bool(excerpt))
                worth = evaluate_worthiness(
                    page_text=excerpt or "",
                    empty=probe.empty,
                    check_junk=probe.check_junk,
                    min_chars=probe.min_chars,
                )
                return pick(
                    not worth.worthy,
                    lambda: _store(
                        _non_content_result(
                            rationale=(
                                "Page has no coherent testable text."
                                f" [worthiness:{worth.reason}]"
                            ),
                            mode=mode,
                        )
                    ),
                    _after_worth,
                )

            return pick(allow_zero, _maybe_zero, _after_worth)

        return pick(isinstance(db_hit, dict) and bool(db_hit), _from_db, _compute)

    return pick(cached is not None, lambda: cached, _after_mem)
