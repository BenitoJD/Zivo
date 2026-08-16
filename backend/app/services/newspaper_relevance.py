"""LLM newspaper exam-relevance judge.

Replaces the former hardcoded keyword allowlist (``_THEME_KEYWORDS`` /
``_OFF_SYLLABUS_MARKERS`` in newspaper_ad_filter) which failed on paraphrased
exam content: a polity page saying "protests / FIRs / Centre" never matched the
literal "supreme court / fundamental rights" keywords. Relevance is now a
*meaning* judgment, not an exact-string ladder — consistent with the "engines,
not if-else" rule (AGENTS.md / ADR 0004).

Mirrors ``vision.judge_page_has_content``: cached by a content hash so retries
never re-bill, pinned to the default text model, and degrades permissively
(allows the page) on any LLM failure so an outage never blocks cooking.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.services.content_worthiness import (
    evaluate_newspaper_relevance_outcome,
    plan_newspaper_relevance_input,
)

logger = logging.getLogger(__name__)

_NEWS_RELEVANCE_SYSTEM = (
    "You judge whether ONE newspaper page is relevant to Indian competitive exams "
    "(UPSC CSE Prelims GS + State PSC Group-1 + central Group-A). The exam syllabus "
    "covers: polity & constitution, economy, geography, environment & ecology, "
    "history & culture, international relations & security, science & technology "
    "policy, social sector & welfare, governance, and disaster management. Judge by "
    "MEANING and subject — a page about governance, rights, policy, courts, protests, "
    "federalism, fiscal or monetary matters, treaties, or public institutions is "
    "relevant even if it never names a textbook keyword. Personal legal notices, "
    "gazette-style name or address changes, routine local weather, crime blotter items "
    "with no policy angle, and publication masthead or registration boilerplate are "
    "NOT relevant. Pure entertainment, sports, celebrity gossip, lifestyle, recipes, "
    "fashion, horoscopes, or event announcements with fees/dates are NOT relevant. "
    "Return JSON only."
)

_NEWS_RELEVANCE_FORMAT = (
    "Is the page above relevant to Indian competitive exams? Reply with exactly one "
    'JSON object: {"relevant": <true|false>, "theme": "<one topic word or empty>", '
    '"rationale": "<one short sentence>"}'
)

_CACHE_KIND = "newspaper_relevance"


def _parse_relevance(raw: str) -> dict[str, Any] | None:
    from app.services.llm_json import extract_json_obj

    parsed = extract_json_obj(raw or "")
    if not parsed:
        return None
    if "relevant" not in parsed:
        return None
    return parsed


def _relevance_payload(
    page_text: str,
    *,
    parsed: dict | None = None,
    judge_error: bool = False,
) -> dict[str, Any]:
    outcome = evaluate_newspaper_relevance_outcome(
        page_text=page_text,
        parsed=parsed,
        judge_error=judge_error,
    )
    return {
        "relevant": outcome.relevant,
        "theme": outcome.theme,
        "rationale": outcome.rationale,
    }


def judge_newspaper_relevance(db: Session, page_text: str) -> dict[str, Any]:
    """Judge whether a newspaper page is exam-relevant.

    Returns ``{"relevant": bool, "theme": str, "rationale": str}``. Cached by the
    page text content hash so the same page never re-bills. On any LLM failure or
    unparseable answer, degrades permissively to relevant=True (the caller's
    downstream usefulness / dedupe / quality gates still filter junk), and caches
    that fallback so retries don't keep calling the model.
    """
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put
    from app.services.llm_registry import default_chat_model_id
    from app.services.llm_router import acomplete_chat
    from app.services.llm_sync import run_coro_in_worker

    cache_key = content_hash_key(_CACHE_KIND, page_text or "")
    hit = cache_get(db, kind=_CACHE_KIND, cache_key=cache_key)
    if isinstance(hit, dict) and "relevant" in hit:
        return {
            "relevant": bool(hit.get("relevant")),
            "theme": str(hit.get("theme") or ""),
            "rationale": str(hit.get("rationale") or ""),
        }

    text = plan_newspaper_relevance_input(page_text)
    if not text:
        verdict = _relevance_payload("")
        cache_put(db, kind=_CACHE_KIND, cache_key=cache_key, value=verdict)
        return verdict

    try:
        raw = run_coro_in_worker(
            acomplete_chat(
                [
                    {"role": "system", "content": _NEWS_RELEVANCE_SYSTEM},
                    {"role": "user", "content": text},
                    {"role": "user", "content": _NEWS_RELEVANCE_FORMAT},
                ],
                db,
                model_id=default_chat_model_id(db),
                log_tag="newspaper_relevance",
            )
        )
    except Exception:
        logger.warning("newspaper relevance judge failed", exc_info=True)
        verdict = _relevance_payload(text, judge_error=True)
        cache_put(db, kind=_CACHE_KIND, cache_key=cache_key, value=verdict)
        return verdict

    parsed = _parse_relevance(raw or "")
    verdict = _relevance_payload(text, parsed=parsed)
    cache_put(db, kind=_CACHE_KIND, cache_key=cache_key, value=verdict)
    return verdict
