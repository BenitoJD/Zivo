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

from app.eta.llm_concurrency import llm_slot_async
from app.services.llm_usage_log import record_llm_usage
from app.services.llm_pool import (
    is_failover_eligible,
    is_llm_pool_enabled,
    iter_chat_model_attempts,
    iter_failover_attempts,
    litellm_provider_kwargs,
    log_failover,
)
from app.services.llm_prompt_cache import apply_prompt_cache
from app.services.llm_registry import ResolvedLlmModel
from app.services.token_budget import (
    CHAT_OUTPUT_MAX_TOKENS,
    OUTPUT_MAX_TOKENS_BATCH,
    OUTPUT_MAX_TOKENS_DEFAULT,
)

logger = logging.getLogger(__name__)

# Retry transient provider errors (esp. 429 rate limits) with exponential backoff
# so a concurrency spike degrades into a brief wait instead of a wasted/failed
# call — protects both reliability and token spend. LiteLLM handles the backoff.
LLM_NUM_RETRIES = int(os.getenv("ZIVO_LLM_NUM_RETRIES", "2"))
CHAT_DEFAULT_MAX_TOKENS = CHAT_OUTPUT_MAX_TOKENS

# Structured JSON / extraction tasks — pin low temperature when model meta omits it.
STRUCTURED_LOG_TAGS = frozenset(
    {
        "page_triage",
        "critic_mcq",
        "rewrite_mcq",
        "generate_mcq",
        "generate_mcq_batch",
        "summarize_chunk",
        "summarize_doc",
        "summarize_rollup",
        "grade_mcq",
    }
)

# Per-call-type output caps when model metadata does not set max_output_tokens.
LOG_TAG_MAX_TOKENS: dict[str, int] = {
    "page_triage": OUTPUT_MAX_TOKENS_DEFAULT,
    "critic_mcq": OUTPUT_MAX_TOKENS_DEFAULT,
    "rewrite_mcq": OUTPUT_MAX_TOKENS_DEFAULT,
    "generate_mcq": OUTPUT_MAX_TOKENS_DEFAULT,
    "generate_mcq_batch": OUTPUT_MAX_TOKENS_BATCH,
    "summarize_chunk": OUTPUT_MAX_TOKENS_DEFAULT,
    "summarize_doc": OUTPUT_MAX_TOKENS_DEFAULT,
    "summarize_rollup": OUTPUT_MAX_TOKENS_DEFAULT,
    "grade_mcq": OUTPUT_MAX_TOKENS_DEFAULT,
    "chat": CHAT_DEFAULT_MAX_TOKENS,
    "complete": CHAT_DEFAULT_MAX_TOKENS,
}


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


def _log_usage(
    tag: str,
    model: str,
    usage: object,
    started: float,
    *,
    db: Session | None = None,
    account_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> None:
    """Emit one structured usage line. Called once per completion."""
    info = _extract_usage(usage)
    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "llm_usage tag=%s model=%s prompt=%d completion=%d cached=%d latency_ms=%d",
        tag,
        model,
        info["prompt_tokens"],
        info["completion_tokens"],
        info["cached_tokens"],
        latency_ms,
    )
    if db is not None:
        record_llm_usage(
            db,
            tag=tag,
            model=model,
            prompt_tokens=info["prompt_tokens"],
            completion_tokens=info["completion_tokens"],
            cached_tokens=info["cached_tokens"],
            latency_ms=latency_ms,
            account_id=account_id,
            document_id=document_id,
        )


def _apply_model(resolved: ResolvedLlmModel, *, log_tag: str = "complete") -> dict:
    kwargs = litellm_provider_kwargs(resolved)
    tag_cap = LOG_TAG_MAX_TOKENS.get(log_tag, CHAT_DEFAULT_MAX_TOKENS)
    meta_cap = kwargs.get("max_tokens")
    if isinstance(meta_cap, int) and meta_cap > 0:
        kwargs["max_tokens"] = max(meta_cap, tag_cap)
    else:
        kwargs["max_tokens"] = tag_cap
    if log_tag in STRUCTURED_LOG_TAGS and "temperature" not in kwargs:
        kwargs["temperature"] = 0.0
    return kwargs


def _prepare_messages(messages: list[dict], resolved: ResolvedLlmModel) -> list[dict]:
    return apply_prompt_cache(messages, provider_slug=resolved.provider.slug)


def _model_attempts(
    db: Session,
    *,
    model_id: uuid.UUID | None,
    require_vision: bool,
    first: ResolvedLlmModel | None = None,
) -> list[ResolvedLlmModel]:
    if model_id is not None:
        return list(iter_chat_model_attempts(db, model_id=model_id, require_vision=require_vision))
    if not is_llm_pool_enabled():
        return list(iter_chat_model_attempts(db, require_vision=require_vision))
    if first is not None:
        return list(iter_failover_attempts(db, start=first, require_vision=require_vision))
    first_model = next(iter(iter_chat_model_attempts(db, require_vision=require_vision)))
    return list(iter_failover_attempts(db, start=first_model, require_vision=require_vision))


async def _stream_chat_impl(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "chat",
    account_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> AsyncIterator[str]:
    import litellm

    last_exc: BaseException | None = None
    attempts = _model_attempts(db, model_id=model_id, require_vision=require_vision)
    for resolved in attempts:
        provider_kwargs = _apply_model(resolved, log_tag=log_tag)
        cached_messages = _prepare_messages(messages, resolved)
        started = time.perf_counter()
        try:
            response = await litellm.acompletion(
                model=resolved.litellm_model,
                messages=cached_messages,
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
            _log_usage(
                log_tag,
                resolved.litellm_model,
                final_usage,
                started,
                db=db,
                account_id=account_id,
                document_id=document_id,
            )
            return
        except Exception as exc:
            last_exc = exc
            if model_id is not None or not is_failover_eligible(exc):
                raise
            log_failover(resolved, exc, log_tag=log_tag)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("stream_chat_completion exhausted model pool without result")


async def stream_chat_completion(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "chat",
    account_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> AsyncIterator[str]:
    """Stream tokens from LiteLLM using the DB model registry."""
    async with llm_slot_async():
        async for chunk in _stream_chat_impl(
            messages,
            db,
            model_id=model_id,
            require_vision=require_vision,
            log_tag=log_tag,
            account_id=account_id,
            document_id=document_id,
        ):
            yield chunk


async def acomplete_chat(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "complete",
    account_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> str:
    """LiteLLM completion without acquiring the process-wide slot (caller owns it)."""
    import litellm

    last_exc: BaseException | None = None
    attempts = _model_attempts(db, model_id=model_id, require_vision=require_vision)
    for resolved in attempts:
        provider_kwargs = _apply_model(resolved, log_tag=log_tag)
        cached_messages = _prepare_messages(messages, resolved)
        started = time.perf_counter()
        try:
            response = await litellm.acompletion(
                model=resolved.litellm_model,
                messages=cached_messages,
                stream=False,
                num_retries=LLM_NUM_RETRIES,
                **provider_kwargs,
            )
            _log_usage(
                log_tag,
                resolved.litellm_model,
                getattr(response, "usage", None),
                started,
                db=db,
                account_id=account_id,
                document_id=document_id,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            last_exc = exc
            if model_id is not None or not is_failover_eligible(exc):
                raise
            log_failover(resolved, exc, log_tag=log_tag)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("complete_chat exhausted model pool without result")


async def complete_chat(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "complete",
    account_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> str:
    async with llm_slot_async():
        return await acomplete_chat(
            messages,
            db,
            model_id=model_id,
            require_vision=require_vision,
            log_tag=log_tag,
            account_id=account_id,
            document_id=document_id,
        )
