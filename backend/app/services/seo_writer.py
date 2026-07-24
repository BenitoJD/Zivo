"""LLM rewrite for SEO posts — Jobs clarity, format mix, soft CTA."""

from __future__ import annotations

import logging
import random
from typing import Any

from sqlalchemy.orm import Session

from app.services.llm_json import extract_json_obj
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.seo_voice import humanize_fields

logger = logging.getLogger(__name__)

_SYSTEM = """You write short educational articles for a learning site called Question Better.
Voice: Steve Jobs clarity. Short sentences. One clear idea. Plain spoken. Human.
Never mention sources, newspapers, uploads, PDFs, or "as an AI".
Never use em-dashes. Never use words like delve, landscape, robust, leverage, tapestry.
No hype. Teach something useful.
Return ONLY JSON."""


def _pick_format() -> str:
    # ~70% explainer, ~30% faq/list
    roll = random.random()
    if roll < 0.70:
        return "explainer"
    if roll < 0.85:
        return "faq"
    return "list"


def _cta_for_stream(stream: str) -> str:
    if stream == "system_design":
        return "system_design"
    return random.choice(["practice", "signup", "system_design"])


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
    fmt = format_override or _pick_format()
    stream = stream if stream in {"general", "system_design"} else "general"
    cta_kind = _cta_for_stream(stream)

    format_rules = {
        "explainer": "Write an explainer (800-1500 words target in body_md). Markdown with short headings.",
        "faq": "Write an FAQ post. body_md with ## questions. Also fill faq_items as [{question, answer}, ...] (3-6 items).",
        "list": "Write a numbered list post (5-9 concrete points). body_md markdown.",
    }[fmt]

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
{source_text[:12000]}
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
    if not title or not body:
        return None

    fields = humanize_fields(title=title, lede=lede, body_md=body)
    faq_items = parsed.get("faq_items") if fmt == "faq" else []
    if not isinstance(faq_items, list):
        faq_items = []
    faq_clean = []
    for item in faq_items[:8]:
        if not isinstance(item, dict):
            continue
        q = humanize_fields(
            title=str(item.get("question") or ""),
            lede="",
            body_md=str(item.get("answer") or ""),
        )
        if q["title"] and q["body_md"]:
            faq_clean.append({"question": q["title"], "answer": q["body_md"]})

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
