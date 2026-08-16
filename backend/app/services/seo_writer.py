"""LLM rewrite for SEO posts — Jobs clarity, format mix, soft CTA."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import choose, pick
from app.services.llm_json import extract_json_obj
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.seo_gate import (
    plan_article_format_contract,
    plan_article_presentation,
    plan_seo_article_source_chars,
    plan_seo_digest_writer_contract,
    plan_seo_faq_item_cap,
)
from app.services.seo_voice import humanize_fields

logger = logging.getLogger(__name__)

_SYSTEM = """You write short educational articles for a learning site called Question Better.
Voice: Steve Jobs clarity. Short sentences. One clear idea. Plain spoken. Human.
Never mention sources, newspapers, uploads, PDFs, or "as an AI".
Never use em-dashes. Never use words like delve, landscape, robust, leverage, tapestry.
No hype. Teach something useful.
Return ONLY JSON."""


def _complete(db: Session, messages: list[dict]) -> str:
    return run_coro_in_worker(
        acomplete_chat(messages, db, log_tag="seo_write")
    )


def write_article(
    db: Session,
    *,
    source_text: str,
    stream: str,
    title_hint: str = "",
    angle_prompt: str = "",
    format_override: str | None = None,
) -> dict[str, Any] | None:
    """Rewrite scrubbed source into public article fields."""
    stream = choose(stream in {"general", "system_design"}, stream, "general")
    presentation = plan_article_presentation(stream, format_override=format_override)
    fmt = presentation.format
    cta_kind = presentation.cta_kind
    format_rules = plan_article_format_contract(fmt).prompt_rule

    source_clip = (source_text or "")[: plan_seo_article_source_chars()]
    user = f"""Rewrite the material below into a public article.

Stream: {stream}
Preferred title angle: {title_hint or "(infer from material)"}
Extra angle: {angle_prompt or "(none)"}
Format: {fmt}
{format_rules}

Soft CTA at end of body_md (1-2 sentences, not salesy):
- practice → invite trying practice questions on Question Better
- signup → invite creating a free account to practice from their own sources
- system_design → invite the System Design practice path
CTA kind to use: {cta_kind}

JSON schema:
{{
  "title": "string",
  "lede": "one sentence hook",
  "body_md": "markdown body including soft CTA at end",
  "topic_fingerprint_hint": "3-8 word topic key",
  "faq_items": [{{"question": "...", "answer": "..."}}]
}}

MATERIAL (already PII-scrubbed; do not reveal origin):
---
{source_clip}
---
"""

    try:
        raw = _complete(
            db,
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        parsed = extract_json_obj(raw)
    except Exception:
        logger.exception("seo write failed")
        return None

    title = str(parsed.get("title") or title_hint or "Untitled").strip()
    lede = str(parsed.get("lede") or "").strip()
    body = str(parsed.get("body_md") or "").strip()

    def _none() -> None:
        return None

    def _fields() -> dict[str, Any]:
        fields = humanize_fields(title=title, lede=lede, body_md=body)
        faq_items = pick(fmt == "faq", lambda: parsed.get("faq_items"), lambda: [])
        faq_items = pick(isinstance(faq_items, list), lambda: faq_items, lambda: [])
        faq_clean = []
        for item in faq_items[: plan_seo_faq_item_cap()]:
            def _add() -> None:
                q = humanize_fields(
                    title=str(item.get("question") or ""),
                    lede="",
                    body_md=str(item.get("answer") or ""),
                )
                pick(
                    bool(q["title"] and q["body_md"]),
                    lambda: faq_clean.append({"question": q["title"], "answer": q["body_md"]}),
                    lambda: None,
                )

            pick(isinstance(item, dict), _add, lambda: None)

        return {
            "title": fields["title"],
            "lede": fields["lede"],
            "body_md": fields["body_md"],
            "format": fmt,
            "stream": stream,
            "cta_kind": cta_kind,
            "topic_fingerprint_hint": str(
                parsed.get("topic_fingerprint_hint") or fields["title"]
            ).strip(),
            "faq_jsonld": faq_clean,
        }

    return pick(not title or not body, _none, _fields)


_EDITION_SYSTEM = """You write daily edition digests for learners preparing for Indian competitive exams.
Voice: a careful human editor. Short clear sentences. Concrete nouns. Occasional contractions.
Write like a study editor who read the day's paper and kept only substantive current-affairs themes.
Cover policy, courts, economy, diplomacy, science, governance, and major national/international events.
Never name any newspaper, publication, or journalist. Never quote headlines verbatim.
Never mention PDFs, uploads, AI, or how this was produced.
Never describe the source material, page layout, masthead, or what you left out.
Never use em-dashes. Avoid: delve, landscape, robust, leverage, game-changer,
it's important to note, in today's fast-paced, tapestry, myriad.
No bullet-heavy walls. Mix paragraph lengths. Teach what happened and why it matters for exams.
Return ONLY JSON."""


def write_edition_digest(
    db: Session,
    *,
    source_text: str,
    edition_date: str,
    title_hint: str = "",
) -> dict[str, Any] | None:
    """Rewrite scrubbed edition text into a public digest that prepares MCQ practice."""
    contract = plan_seo_digest_writer_contract()
    excerpt = source_text[: contract.source_chars]
    user = f"""Write a digest for learners who will practice MCQs on this day's edition.

Edition date: {edition_date}
Working title hint: {title_hint or "(infer a clear title from the themes)"}

Format: explainer digest ({contract.min_words}-{contract.max_words} words in body_md). Markdown with 2-4 short ## headings.
Cover only substantive current-affairs themes from the material below.
The material is already pre-filtered for exam relevance; still omit any stray filler.
No source attribution. Do not mention personal notices, weather, ads, or publication metadata.

End body_md with one short paragraph inviting practice on Question Better (not salesy).

JSON schema:
{{
  "title": "string (specific, not generic 'Daily Digest')",
  "lede": "one sentence hook",
  "body_md": "markdown digest including soft practice invite at end",
  "topic_fingerprint_hint": "3-8 word topic key"
}}

EDITION MATERIAL (PII-scrubbed; do not reveal origin):
---
{excerpt}
---
"""

    try:
        from app.services.chunk_map_cache import content_hash_key
        from app.services.generation_cache import get as cache_get, put as cache_put

        digest_key = content_hash_key(
            "seo_digest", _EDITION_SYSTEM, edition_date, title_hint, excerpt
        )
        hit = cache_get(db, kind="seo_digest", cache_key=digest_key)
        parsed = None

        def _from_cache() -> dict[str, Any] | None:
            cached = extract_json_obj(hit)
            title = str(cached.get("title") or title_hint or "Edition digest").strip()
            lede = str(cached.get("lede") or "").strip()
            body = str(cached.get("body_md") or "").strip()

            def _hit() -> dict[str, Any]:
                fields = humanize_fields(title=title, lede=lede, body_md=body)
                return {
                    "title": fields["title"],
                    "lede": fields["lede"],
                    "body_md": fields["body_md"],
                    "format": "explainer",
                    "stream": "general",
                    "cta_kind": "practice",
                    "topic_fingerprint_hint": str(
                        cached.get("topic_fingerprint_hint") or fields["title"]
                    ).strip(),
                    "faq_jsonld": [],
                }

            return pick(bool(title and body), _hit, lambda: None)

        cached_out = pick(
            isinstance(hit, str) and bool(hit.strip()),
            _from_cache,
            lambda: None,
        )

        def _generate() -> dict[str, Any] | None:
            nonlocal parsed
            raw = _complete(
                db,
                [
                    {"role": "system", "content": _EDITION_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            parsed = extract_json_obj(raw)
            pick(
                bool(parsed.get("title") and parsed.get("body_md")),
                lambda: cache_put(db, kind="seo_digest", cache_key=digest_key, value=raw),
                lambda: None,
            )
            return None

        pick(cached_out is not None, lambda: None, _generate)
    except Exception:
        logger.exception("edition digest write failed")
        return None

    def _cached() -> dict[str, Any] | None:
        return cached_out

    def _from_parsed() -> dict[str, Any] | None:
        title = str(parsed.get("title") or title_hint or "Edition digest").strip()
        lede = str(parsed.get("lede") or "").strip()
        body = str(parsed.get("body_md") or "").strip()

        def _empty() -> None:
            logger.warning(
                "edition digest write returned empty fields title=%r body_len=%s",
                title,
                len(body),
            )
            return None

        def _ok() -> dict[str, Any]:
            fields = humanize_fields(title=title, lede=lede, body_md=body)
            return {
                "title": fields["title"],
                "lede": fields["lede"],
                "body_md": fields["body_md"],
                "format": "explainer",
                "stream": "general",
                "cta_kind": "practice",
                "topic_fingerprint_hint": str(
                    parsed.get("topic_fingerprint_hint") or fields["title"]
                ).strip(),
                "faq_jsonld": [],
            }

        return pick(not title or not body, _empty, _ok)

    return pick(cached_out is not None, _cached, _from_parsed)
