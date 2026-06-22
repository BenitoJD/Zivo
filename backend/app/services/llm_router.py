"""LiteLLM routing — chat completion + token/cost usage logging.

Every completion (streaming or not) emits one structured log line with
prompt/completion/cached token counts and latency. This is the before/after
ruler for compute-cost optimization work (provider prompt caching, retrieval
gating, semantic cache).
"""

from collections.abc import AsyncIterator
import logging
import os
import time
import uuid

from sqlalchemy.orm import Session

from app.services.llm_registry import ResolvedLlmModel, configure_litellm, resolve_chat_model

logger = logging.getLogger(__name__)

# Retry transient provider errors (esp. 429 rate limits) with exponential backoff
# so a concurrency spike degrades into a brief wait instead of a wasted/failed
# call — protects both reliability and token spend. LiteLLM handles the backoff.
LLM_NUM_RETRIES = int(os.getenv("ZIVO_LLM_NUM_RETRIES", "4"))


def _extract_usage(usage: object) -> dict:
    """Normalize an LiteLLM/OpenAI usage object into a flat dict.

    Cached-token counts live under provider-specific sub-objects
    (prompt_tokens_details.cached_tokens on OpenAI/MiMo). We try several
    attribute paths and fall back to 0.
    """
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}

    def _get(obj: object, *path, default=0) -> int:
        cur: object = obj
        for key in path:
            cur = getattr(cur, key, None) if not isinstance(cur, dict) else cur.get(key)
            if cur is None:
                return default
        try:
            return int(cur)
        except (TypeError, ValueError):
            return default

    cached = _get(usage, "prompt_tokens_details", "cached_tokens")
    if not cached:
        # Some providers nest differently.
        cached = _get(usage, "prompt_cache_hit_tokens") or _get(usage, "cache_hit_tokens")
    return {
        "prompt_tokens": _get(usage, "prompt_tokens"),
        "completion_tokens": _get(usage, "completion_tokens"),
        "cached_tokens": cached,
    }


def _log_usage(tag: str, model: str, usage: object, started: float) -> None:
    """Emit one structured usage line. Called once per completion."""
    info = _extract_usage(usage)
    logger.info(
        "llm_usage tag=%s model=%s prompt=%d completion=%d cached=%d latency_ms=%d",
        tag,
        model,
        info["prompt_tokens"],
        info["completion_tokens"],
        info["cached_tokens"],
        int((time.perf_counter() - started) * 1000),
    )


async def stream_chat_completion(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "chat",
) -> AsyncIterator[str]:
    """Stream tokens from LiteLLM using the DB model registry."""
    import litellm

    resolved = resolve_chat_model(db, model_id=model_id, require_vision=require_vision)
    _apply_model(resolved)
    started = time.perf_counter()
    response = await litellm.acompletion(
        model=resolved.litellm_model,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        num_retries=LLM_NUM_RETRIES,
    )
    final_usage: object = None
    async for chunk in response:
        # The usage object rides on a trailing chunk (choices may be empty/None).
        if getattr(chunk, "usage", None):
            final_usage = chunk.usage
        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = choices[0].delta.content or ""
            if delta:
                yield delta
    _log_usage(log_tag, resolved.litellm_model, final_usage, started)


async def complete_chat(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "complete",
) -> str:
    import litellm

    resolved = resolve_chat_model(db, model_id=model_id, require_vision=require_vision)
    _apply_model(resolved)
    started = time.perf_counter()
    response = await litellm.acompletion(
        model=resolved.litellm_model,
        messages=messages,
        stream=False,
        num_retries=LLM_NUM_RETRIES,
    )
    _log_usage(log_tag, resolved.litellm_model, getattr(response, "usage", None), started)
    return response.choices[0].message.content or ""


def _apply_model(resolved: ResolvedLlmModel) -> None:
    configure_litellm(resolved.provider)
    if not resolved.record.supports_streaming:
        pass  # caller may still use non-streaming complete_chat
