"""LiteLLM routing — chat completion + token/cost usage logging.

Every completion (streaming or not) emits one structured log line with
prompt/completion/cached token counts and latency. This is the before/after
ruler for compute-cost optimization work (provider prompt caching, retrieval
gating, semantic cache).

When ``model_id`` is omitted and the pool is enabled, requests go DEFAULT-FIRST:
the DB-default model takes every call (keeping its provider prefix cache hot)
and the rest of the pool is used only for failover on transient provider errors.
"""

from collections.abc import AsyncIterator
import asyncio
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
    record_failover,
)
from app.services.llm_prompt_cache import apply_prompt_cache
from app.services.llm_registry import ResolvedLlmModel
from app.services.llm_router_pool import (
    build_router,
    router_enabled,
)
from app.services.llm_prose_engine import sanitize_llm_output
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
CHAT_FAILOVER_MAX = int(os.getenv("ZIVO_CHAT_FAILOVER_MAX", "2"))
CHAT_REQUEST_TIMEOUT = int(os.getenv("ZIVO_CHAT_REQUEST_TIMEOUT", "90"))
# Background generation (triage/MCQ/critic/rewrite) is non-streaming and normally
# completes in ~10-25s. A tight timeout means an intermittently-stalling provider
# fails over to the next model (e.g. step-3.5-flash → step-3.7-flash) in seconds
# instead of stalling the learner's first question. Interactive chat keeps the longer
# budget since it streams and can legitimately run longer.
GENERATION_REQUEST_TIMEOUT = int(os.getenv("ZIVO_GENERATION_REQUEST_TIMEOUT", "35"))
_GENERATION_LOG_TAGS = frozenset(
    {
        "page_triage",
        "generate_mcq",
        "generate_mcq_batch",
        "critic_mcq",
        "rewrite_mcq",
        "topics_extract",
        "topics_rollup",
        "page_vision_judge",
    }
)
# Long-form generations (study notes, explanations, flashcard decks, memory palaces)
# legitimately produce large outputs that can run well past the tight failover budget.
# They get the longer chat budget instead — failing over would just re-incur the long
# generation, and a 35s cap was truncating/timing them out on slower providers.
_LONGFORM_GENERATION_LOG_TAGS = frozenset(
    {
        "topic_explain",
        "notes_generate",
        "notes_rollup",
        "flashcards_generate",
        "memory_palace_generate",
        "quiz_generate",
        "seo_write",
        "lesson_page",
    }
)


def _request_timeout_for(log_tag: str) -> int:
    if log_tag in _GENERATION_LOG_TAGS:
        return GENERATION_REQUEST_TIMEOUT  # short structured gen — fast failover
    # Long-form generation (notes/explain/cards/palace) and interactive chat both
    # legitimately run longer, so they get the longer budget.
    if log_tag in _LONGFORM_GENERATION_LOG_TAGS:
        return CHAT_REQUEST_TIMEOUT
    return CHAT_REQUEST_TIMEOUT

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
        # Grading + transcription are judged, not authored — pin deterministic (temp 0)
        # for consistent, reproducible marks. (mains_gen stays warm for question variety.)
        "mains_grade",
        "mains_ocr",
        "page_vision_judge",
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
    # One ~80-110 word coaching paragraph. The default 8192 let the model ramble to
    # ~1,170 tokens (~9.8s p50) on the answer path; 512 bounds worst-case latency
    # (~4-5s) with enough headroom that normal feedback isn't truncated mid-sentence.
    "grade_mcq": int(os.getenv("ZIVO_GRADE_MAX_TOKENS", "512")),
    # Batched per-option coaching (one blurb per option in a JSON map), generated
    # off the answer path — needs room for up to ~6 options.
    "coach_mcq": int(os.getenv("ZIVO_COACH_MAX_TOKENS", "1200")),
    # Per-page Learn lesson (~150-250 words of teaching prose + title, fenced as
    # JSON). Generated before the MCQ loop, so it is never on the answer path.
    # SEO articles and edition digests are long-form JSON (title + lede + body_md).
    "seo_write": int(os.getenv("ZIVO_SEO_WRITE_MAX_TOKENS", "6144")),
    "lesson_page": int(os.getenv("ZIVO_LESSON_MAX_TOKENS", "900")),
    "chat": CHAT_DEFAULT_MAX_TOKENS,
    "complete": CHAT_DEFAULT_MAX_TOKENS,
    "mains_gen": 1400,
    "mains_grade": 1800,
    "mains_ocr": 3000,
    "page_vision_judge": 256,
    # Memory palace: 6-8 stations × 5 detailed fields (locus/term/fact/image/cue).
    # The 2048 chat default truncates the JSON mid-array, so `_finalize` drops the
    # whole palace and the build fails with no_palace_generated. Generous cap so
    # the full journey parses; generated off the answer path (background worker).
    "memory_palace_generate": 6000,
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
    # coding_assist is chat-shaped: it gets its own usage tag purely for cost
    # attribution and must keep chat's cap/failover behavior.
    if log_tag in {"chat", "complete", "coding_assist"}:
        if isinstance(meta_cap, int) and meta_cap > 0:
            kwargs["max_tokens"] = min(meta_cap, tag_cap)
        else:
            kwargs["max_tokens"] = tag_cap
    elif isinstance(meta_cap, int) and meta_cap > 0:
        kwargs["max_tokens"] = max(meta_cap, tag_cap)
    else:
        kwargs["max_tokens"] = tag_cap
    if log_tag in STRUCTURED_LOG_TAGS and "temperature" not in kwargs:
        kwargs["temperature"] = 0.0
    # Every tag gets a request timeout. Previously only chat/complete did, so
    # page_triage / generate_mcq / critic_mcq calls had none and a stalled provider
    # hung the generation worker indefinitely. Generation uses a tighter budget so a
    # stalled provider fails over fast.
    kwargs.setdefault("timeout", _request_timeout_for(log_tag))
    return kwargs


def _prepare_messages(messages: list[dict], resolved: ResolvedLlmModel) -> list[dict]:
    return apply_prompt_cache(messages, provider_slug=resolved.provider.slug)


def _model_attempts(
    db: Session,
    *,
    model_id: uuid.UUID | None,
    require_vision: bool,
    first: ResolvedLlmModel | None = None,
    log_tag: str = "complete",
) -> list[ResolvedLlmModel]:
    if model_id is not None:
        return list(iter_chat_model_attempts(db, model_id=model_id, require_vision=require_vision))
    if not is_llm_pool_enabled():
        return list(iter_chat_model_attempts(db, require_vision=require_vision))
    if first is not None:
        attempts = list(iter_failover_attempts(db, start=first, require_vision=require_vision))
    else:
        first_model = next(iter(iter_chat_model_attempts(db, require_vision=require_vision)))
        attempts = list(iter_failover_attempts(db, start=first_model, require_vision=require_vision))
    if log_tag in {"chat", "coding_assist"} and CHAT_FAILOVER_MAX > 0:
        return attempts[:CHAT_FAILOVER_MAX]
    return attempts


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

    litellm.drop_params = True  # drop provider-unsupported params (e.g. 'thinking') instead of erroring
    last_exc: BaseException | None = None
    attempts = _model_attempts(
        db, model_id=model_id, require_vision=require_vision, log_tag=log_tag
    )
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
            try:
                async for chunk in response:
                    if getattr(chunk, "usage", None):
                        final_usage = chunk.usage
                    choices = getattr(chunk, "choices", None) or []
                    if choices:
                        delta = choices[0].delta.content or ""
                        if delta:
                            yield delta
            except BaseException:
                # Client disconnect raises GeneratorExit/CancelledError at the
                # yield — BaseExceptions the failover clause below never sees — and
                # the provider still billed everything generated so far. Log under
                # a separate tag so completed-stream cache stats stay clean.
                # (Usage rides the FINAL chunk, so this is often a 0-token row:
                # it counts the call, not the abandoned tokens.)
                _log_usage(
                    f"{log_tag}_aborted",
                    resolved.litellm_model,
                    final_usage,
                    started,
                    db=db,
                    account_id=account_id,
                    document_id=document_id,
                )
                raise
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
            record_failover(resolved, exc, log_tag=log_tag)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("stream_chat_completion exhausted model pool without result")


# LLM Prose Engine — every completion/chunk passes sanitize_llm_output (see
# docs/LLM_PROSE_ENGINE.md). Verbatim opt-out via strip_output=False (OCR, etc.).


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
            yield sanitize_llm_output(chunk).text


async def acomplete_chat(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "complete",
    account_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    strip_output: bool = True,
) -> str:
    """LiteLLM completion without acquiring the process-wide slot (caller owns it).

    `strip_output` normalizes em/en dashes in the result; pass False when the output
    must be verbatim (e.g. OCR transcription of a user's answer)."""
    import litellm

    litellm.drop_params = True  # drop provider-unsupported params (e.g. 'thinking') instead of erroring
    last_exc: BaseException | None = None
    attempts = _model_attempts(
        db, model_id=model_id, require_vision=require_vision, log_tag=log_tag
    )

    # Router-backed path: the DB-default model first, automatic failover +
    # per-model cooldown on rate limits/timeouts, then return to the default
    # after the cooldown. Only used when no explicit model is pinned and the
    # router is enabled — pinned models keep the deterministic manual path.
    if model_id is None and router_enabled():
        try:
            return await _router_complete(
                attempts,
                messages=messages,
                db=db,
                log_tag=log_tag,
                account_id=account_id,
                document_id=document_id,
                strip_output=strip_output,
            )
        except Exception as exc:
            # Router raised (all models cooled down / hard error): fall back to
            # the manual loop below so a router hiccup never loses a call.
            logger.warning("router completion failed; falling back to manual loop: %s", exc)

    for resolved in attempts:
        provider_kwargs = _apply_model(resolved, log_tag=log_tag)
        cached_messages = _prepare_messages(messages, resolved)
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=resolved.litellm_model,
                    messages=cached_messages,
                    stream=False,
                    num_retries=LLM_NUM_RETRIES,
                    **provider_kwargs,
                ),
                # Hard backstop tracks the per-tag request timeout so a stalled
                # generation attempt aborts fast and fails over to the next model.
                timeout=_request_timeout_for(log_tag) + 15,
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
            content = response.choices[0].message.content or ""
            return (
                sanitize_llm_output(content).text
                if strip_output
                else content
            )
        except Exception as exc:
            last_exc = exc
            # A hard-timeout (stalled provider) is failover-eligible: try the next
            # model rather than raising, so one wedged provider can't kill generation.
            timed_out = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            if model_id is not None or (not timed_out and not is_failover_eligible(exc)):
                raise
            record_failover(resolved, exc, log_tag=log_tag)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("complete_chat exhausted model pool without result")


# --- Router-backed completion (cooldown + automatic failover) -----------------

_router_cache: dict[str, object] = {}
_router_lock = asyncio.Lock()


def _router_for_attempts(attempts: list[ResolvedLlmModel]):
    """Return a cached Router for the given pool, rebuilding when the pool changes.

    Keyed by the ordered litellm model ids — when the admin toggles a model or
    the env bootstrap changes the default, the next call rebuilds the router.
    """
    key = "|".join(m.litellm_model for m in attempts)
    cached = _router_cache.get(key)
    if cached is not None:
        return cached
    router = build_router(attempts)
    _router_cache[key] = router
    return router


async def _router_complete(
    attempts: list[ResolvedLlmModel],
    *,
    messages: list[dict],
    db: Session | None,
    log_tag: str,
    account_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
    strip_output: bool,
) -> str:
    """One completion through the litellm Router: default model first, automatic
    failover on rate limits/timeouts, per-model cooldown, then return to the
    default once the cooldown expires."""
    if not attempts:
        raise RuntimeError("no chat models in pool")
    router = _router_for_attempts(attempts)
    # Forward only per-CALL knobs. Request-level api_key/api_base are treated by
    # litellm as clientside credentials that override EVERY fallback deployment's
    # own credentials — cross-provider failover would hit the primary's endpoint
    # with the primary's key. The deployments (router_model_list) already carry
    # per-model credentials and params.
    full_kwargs = _apply_model(attempts[0], log_tag=log_tag)
    provider_kwargs = {
        k: v for k, v in full_kwargs.items() if k in {"max_tokens", "temperature", "timeout"}
    }
    started = time.perf_counter()
    response = await asyncio.wait_for(
        router.acompletion(
            model=attempts[0].litellm_model,
            # Same cache treatment as the manual/streaming paths: passthrough for
            # DeepSeek/OpenAI-compatible, cache_control breakpoints for Anthropic.
            # (If an Anthropic primary fails over inside the Router to an
            # OpenAI-compatible deployment, the annotated content blocks ride
            # along; litellm drops unsupported params.)
            messages=_prepare_messages(messages, attempts[0]),
            stream=False,
            **provider_kwargs,
        ),
        timeout=_request_timeout_for(log_tag) + 30,
    )
    # Router may have failed over internally. response.model is the provider-raw
    # name ("deepseek-v4-flash"); map it back to the registry string
    # ("openai/deepseek-v4-flash") so qb.llm_usage_event has ONE label per model.
    raw_model = str(getattr(response, "model", "") or "")
    used_model = next(
        (
            m.litellm_model
            for m in attempts
            if m.litellm_model == raw_model or m.litellm_model.endswith("/" + raw_model)
        ),
        attempts[0].litellm_model,
    )
    _log_usage(
        log_tag,
        used_model,
        getattr(response, "usage", None),
        started,
        db=db,
        account_id=account_id,
        document_id=document_id,
    )
    content = response.choices[0].message.content or ""
    return sanitize_llm_output(content).text if strip_output else content


async def complete_chat(
    messages: list[dict],
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
    log_tag: str = "complete",
    account_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    strip_output: bool = True,
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
            strip_output=strip_output,
        )
