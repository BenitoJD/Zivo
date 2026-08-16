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

from app.engine_runtime import pick
from app.services.mcq_dedup import SUBJECT_MATTER_PREFIX

# User messages whose leading prefix is stable across generate/critic/rewrite/triage.
_STABLE_PREFIX_MARKERS = (
    SUBJECT_MATTER_PREFIX,
    "Document excerpts",
    "Section summaries:",
    "Page text:",  # page_triage stable page prefix
    "Grounding context",  # grade_mcq doc context
)


def _anthropic_cached_block(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _should_cache_message(msg: dict[str, Any], index: int) -> bool:
    role = msg.get("role")
    content = msg.get("content")
    return pick(
        not isinstance(content, str) or not content.strip(),
        lambda: False,
        lambda: pick(
            role == "system",
            lambda: True,
            lambda: bool(
                role == "user"
                and index <= 3
                and any(content.startswith(marker) for marker in _STABLE_PREFIX_MARKERS)
            ),
        ),
    )


def apply_prompt_cache(messages: list[dict], *, provider_slug: str) -> list[dict]:
    """Return a copy of *messages* with cache breakpoints where supported."""

    def _apply() -> list[dict]:
        out: list[dict] = []
        for i, msg in enumerate(messages):
            content = msg.get("content")
            pick(
                not _should_cache_message(msg, i) or not isinstance(content, str),
                lambda: out.append(msg),
                lambda: out.append({**msg, "content": _anthropic_cached_block(content)}),
            )
        return out

    return pick(provider_slug != "anthropic", lambda: messages, _apply)
