"""Provider-specific prompt-prefix cache breakpoints.

Anthropic requires explicit ``cache_control`` on message blocks; OpenAI-compatible
providers (DeepSeek, MiMo, Z.AI) cache stable leading prefixes automatically when
message ordering is stable across calls — DeepSeek in particular needs NO request
opt-in and reports hits via ``usage.prompt_cache_hit_tokens`` (picked up by
``llm_router._extract_usage``), so the correct behaviour for it is the untouched
passthrough below.
"""

from __future__ import annotations

from typing import Any

from app.services.mcq_dedup import SUBJECT_MATTER_PREFIX

# User messages whose leading prefix is stable across generate/critic/rewrite/triage.
_STABLE_PREFIX_MARKERS = (SUBJECT_MATTER_PREFIX, "Document excerpts", "Section summaries:")


def _anthropic_cached_block(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _should_cache_message(msg: dict[str, Any], index: int) -> bool:
    role = msg.get("role")
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        return False
    if role == "system":
        return True
    if role == "user" and index <= 3:
        return any(content.startswith(marker) for marker in _STABLE_PREFIX_MARKERS)
    return False


def apply_prompt_cache(messages: list[dict], *, provider_slug: str) -> list[dict]:
    """Return a copy of *messages* with cache breakpoints where supported."""
    if provider_slug != "anthropic":
        return messages

    out: list[dict] = []
    for i, msg in enumerate(messages):
        if not _should_cache_message(msg, i):
            out.append(msg)
            continue
        content = msg["content"]
        # Real check (not `assert`, which -O strips): only str content is cacheable;
        # pass anything else through untouched rather than mis-wrapping it.
        if not isinstance(content, str):
            out.append(msg)
            continue
        cached = dict(msg)
        cached["content"] = _anthropic_cached_block(content)
        out.append(cached)
    return out
