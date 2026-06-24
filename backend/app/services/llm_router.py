"""LiteLLM routing — chat completion + token/cost usage logging.

Every completion (streaming or not) emits one structured log line with
prompt/completion/cached token counts and latency. This is the before/after
ruler for compute-cost optimization work (provider prompt caching, retrieval
gating, semantic cache).

When ``model_id`` is omitted and the pool is enabled, requests round-robin
across all configured chat models and fail over on transient provider errors.
"""

from collections.abc import AsyncIterator
import logging
import os
import time
import uuid

from sqlalchemy.orm import Session

from app.eta.llm_concurrency import get_llm_semaphore
from app.services.llm_pool import (
    is_failover_eligible,
    iter_chat_model_attempts,
    litellm_provider_kwargs,
    log_failover,
)
from app.services.llm_registry import ResolvedLlmModel

logger = logging.getLogger(__name__)

# Retry transient provider errors (esp. 429 rate limits) with exponential backoff
# so a concurrency spike degrades into a brief wait instead of a wasted/failed
# call — protects both reliability and token spend. LiteLLM handles the backoff.
LLM_NUM_RETRIES = int(os.getenv("ZIVO_LLM_NUM_RETRIES", "2"))
CHAT_DEFAULT_MAX_TOKENS = int(os.getenv("ZIVO_CHAT_MAX_TOKENS", "1024"))


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


def _apply_model(resolved: ResolvedLlmModel) -> dict:
    kwargs = litellm_provider_kwargs(resolved)
    if "max_tokens" not in kwargs:
        kwargs["max_tokens"] = CHAT_DEFAULT_MAX_TOKENS
    return kwargs


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

    last_exc: BaseException | None = None
    async with get_llm_semaphore():
        for resolved in iter_chat_model_attempts(db, model_id=model_id, require_vision=require_vision):
            provider_kwargs = _apply_model(resolved)
            started = time.perf_counter()
            try:
                response = await litellm.acompletion(
                    model=resolved.litellm_model,
                    messages=messages,
                    stream=True,
                    stream_options={"include_usage": True},
                    num_retries=LLM_NUM_RETRIES,
                    **provider_kwargs,
                )
                final_usage: object = None
                async for chunk in response:
                    if getattr(chunk, "usage", None):
                        final_usage = chunk.usage
                    choices = getattr(chunk, "choices", None) or []
                    if choices:
                        delta = choices[0].delta.content or ""
                        if delta:
                            yield delta
                _log_usage(log_tag, resolved.litellm_model, final_usage, started)
                return
            except Exception as exc:
                last_exc = exc
                if model_id is not None or not is_failover_eligible(exc):
                    raise
                log_failover(resolved, exc, log_tag=log_tag)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("stream_chat_completion exhausted model pool without result")


async def complete_chat(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "complete",
) -> str:
    import litellm

    last_exc: BaseException | None = None
    async with get_llm_semaphore():
        for resolved in iter_chat_model_attempts(db, model_id=model_id, require_vision=require_vision):
            provider_kwargs = _apply_model(resolved)
            started = time.perf_counter()
            try:
                response = await litellm.acompletion(
                    model=resolved.litellm_model,
                    messages=messages,
                    stream=False,
                    num_retries=LLM_NUM_RETRIES,
                    **provider_kwargs,
                )
                _log_usage(log_tag, resolved.litellm_model, getattr(response, "usage", None), started)
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_exc = exc
                if model_id is not None or not is_failover_eligible(exc):
                    raise
                log_failover(resolved, exc, log_tag=log_tag)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("complete_chat exhausted model pool without result")
