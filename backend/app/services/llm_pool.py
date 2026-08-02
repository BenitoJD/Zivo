"""Round-robin chat model pool with failover across configured providers."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.llm_registry import ResolvedLlmModel, configure_litellm

logger = logging.getLogger(__name__)

_pool_enabled_override: bool | None = None


def is_llm_pool_enabled() -> bool:
    if _pool_enabled_override is not None:
        return _pool_enabled_override
    return get_settings().llm_pool_enabled


def set_llm_pool_enabled(enabled: bool) -> None:
    global _pool_enabled_override
    _pool_enabled_override = enabled


def _provider_ready(provider: LlmProvider) -> bool:
    if provider.slug == "local":
        return True
    return bool((provider.api_key or "").strip())


def list_pool_chat_models(
    db: Session,
    *,
    require_vision: bool = False,
) -> list[ResolvedLlmModel]:
    """Enabled chat models whose provider has credentials, ordered for routing."""
    query = (
        db.query(LlmModel)
        .join(LlmProvider)
        .options(joinedload(LlmModel.provider))
        .filter(
            LlmModel.kind == LlmModelKind.chat,
            LlmModel.is_enabled.is_(True),
            LlmProvider.is_enabled.is_(True),
        )
        .order_by(LlmModel.sort_order, LlmModel.display_name)
    )
    if require_vision:
        query = query.filter(LlmModel.supports_image_input.is_(True))
    else:
        # Vision-only models never serve ordinary text or text failover — a metered
        # vision model is billed for vision alone.
        query = query.filter(LlmModel.vision_only.is_(False))

    out: list[ResolvedLlmModel] = []
    for model in query.all():
        if _provider_ready(model.provider):
            out.append(ResolvedLlmModel(record=model, provider=model.provider))
    return out


def iter_failover_attempts(
    db: Session,
    *,
    start: ResolvedLlmModel,
    require_vision: bool = False,
) -> Iterator[ResolvedLlmModel]:
    """Yield models to try after a transient error — same provider first.

    Cross-provider failover re-bills the full uncached prefix; exhausting
    same-provider siblings first preserves provider-side prefix caches.
    """
    pool = list_pool_chat_models(db, require_vision=require_vision)
    if not pool:
        yield start
        return

    provider_id = start.provider.id
    same_provider = [m for m in pool if m.provider.id == provider_id]
    other_provider = [m for m in pool if m.provider.id != provider_id]

    seen: set[uuid.UUID] = set()
    for candidate in (start, *same_provider, *other_provider):
        mid = candidate.record.id
        if mid in seen:
            continue
        seen.add(mid)
        yield candidate


def iter_chat_model_attempts(
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
) -> Iterator[ResolvedLlmModel]:
    """Yield models to try — explicit pick, or default-first with pool failover.

    Every call starts at the pinned default (falling back to pool order) instead
    of round-robining: one provider takes all traffic, so its server-side prompt
    prefix cache stays hot (DeepSeek et al. bill cache hits at a fraction of the
    uncached rate). The rest of the pool remains as failover only.
    """
    if model_id is not None:
        from app.services.llm_registry import resolve_chat_model

        yield resolve_chat_model(db, model_id=model_id, require_vision=require_vision)
        return

    if not is_llm_pool_enabled():
        from app.services.llm_registry import resolve_chat_model

        yield resolve_chat_model(db, require_vision=require_vision)
        return

    pool = list_pool_chat_models(db, require_vision=require_vision)
    if not pool:
        raise HTTPException(status_code=503, detail="No chat model configured")

    start = next((i for i, m in enumerate(pool) if m.record.is_default), 0)
    for offset in range(len(pool)):
        yield pool[(start + offset) % len(pool)]


def is_failover_eligible(exc: BaseException) -> bool:
    """Whether to try the next model in the pool after this error."""
    try:
        from litellm.exceptions import (
            APIConnectionError,
            APIError,
            RateLimitError,
            ServiceUnavailableError,
            Timeout,
        )
    except ImportError:
        RateLimitError = Timeout = ServiceUnavailableError = APIConnectionError = APIError = ()  # type: ignore[misc, assignment]

    if isinstance(exc, (RateLimitError, Timeout, ServiceUnavailableError, APIConnectionError)):
        return True

    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int) and status in {401, 402, 403, 408, 409, 429, 500, 502, 503, 504}:
        return True

    if isinstance(exc, APIError):
        return True

    name = type(exc).__name__.lower()
    if any(token in name for token in ("timeout", "rate", "unavailable", "connection")):
        return True

    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "rate limit",
            "too many requests",
            "timeout",
            "timed out",
            "overloaded",
            "unavailable",
            "connection error",
            "insufficient balance",
            "billing",
            "payment required",
            "invalid api key",
            "authentication",
            "403",
            "402",
            "401",
            "503",
            "502",
            "429",
        )
    )


def litellm_provider_kwargs(resolved: ResolvedLlmModel) -> dict[str, str]:
    """Per-request provider credentials + model-level call params.

    Credentials (avoids env-var races between providers). Model-level params
    are read from the model row's ``meta`` JSONB so per-model behavior — e.g.
    disabling a hosted model's reasoning/thinking mode or capping output tokens
    — is data-driven rather than hardcoded. Only keys litellm understands are
    forwarded; unknown keys are ignored.

    Supported ``meta`` keys:
    - ``thinking_disabled`` (bool): emit ``thinking={"type": "disabled"}`` and an
      OpenAI-compatible ``extra_body`` fallback (Z.AI GLM 4.x emits a 1–3k-token
      reasoning trace by default that we discard — disabling it is a ~5× latency win).
    - ``max_tokens`` (int): hard cap on completion tokens.
    - ``temperature`` (float): sampling temperature.
    """
    provider = resolved.provider
    params: dict = dict(configure_litellm(provider))
    if provider.api_key:
        params["api_key"] = provider.api_key
    if provider.api_base_url:
        params["api_base"] = str(provider.api_base_url).rstrip("/")

    meta = resolved.record.meta or {}
    if not isinstance(meta, dict):
        meta = {}

    if meta.get("thinking_disabled") and resolved.provider.slug == "zai":
        # Z.AI native form (honored by the GLM 4.x OpenAI-compatible endpoint).
        params["thinking"] = {"type": "disabled"}
        # OpenAI-compatible / vLLM fallback for the same switch.
        params.setdefault("extra_body", {})
        if isinstance(params.get("extra_body"), dict):
            params["extra_body"].setdefault("chat_template_kwargs", {})
            params["extra_body"]["chat_template_kwargs"].setdefault("enable_thinking", False)

    max_tokens = meta.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        params["max_tokens"] = max_tokens

    temperature = meta.get("temperature")
    if isinstance(temperature, (int, float)):
        params["temperature"] = temperature

    return params


def log_failover(resolved: ResolvedLlmModel, exc: BaseException, *, log_tag: str) -> None:
    logger.warning(
        "llm_failover tag=%s model=%s provider=%s error=%s",
        log_tag,
        resolved.litellm_model,
        resolved.provider.slug,
        exc,
    )
