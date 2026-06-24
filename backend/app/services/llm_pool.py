"""Round-robin chat model pool with failover across configured providers."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.llm_registry import ResolvedLlmModel

logger = logging.getLogger(__name__)

_pool_lock = threading.Lock()
_pool_cursor = 0
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

    out: list[ResolvedLlmModel] = []
    for model in query.all():
        if _provider_ready(model.provider):
            out.append(ResolvedLlmModel(record=model, provider=model.provider))
    return out


def iter_chat_model_attempts(
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
) -> Iterator[ResolvedLlmModel]:
    """Yield models to try — explicit pick, or round-robin across the pool."""
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

    global _pool_cursor
    with _pool_lock:
        start = _pool_cursor % len(pool)
        _pool_cursor = (_pool_cursor + 1) % len(pool)

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
    if isinstance(status, int) and status in {408, 409, 429, 500, 502, 503, 504}:
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
            "503",
            "502",
            "429",
        )
    )


def litellm_provider_kwargs(resolved: ResolvedLlmModel) -> dict[str, str]:
    """Per-request provider credentials (avoids env-var races between providers)."""
    provider = resolved.provider
    params: dict[str, str] = {}
    if provider.api_key:
        params["api_key"] = provider.api_key
    if provider.api_base_url:
        params["api_base"] = str(provider.api_base_url).rstrip("/")
    return params


def log_failover(resolved: ResolvedLlmModel, exc: BaseException, *, log_tag: str) -> None:
    logger.warning(
        "llm_failover tag=%s model=%s provider=%s error=%s",
        log_tag,
        resolved.litellm_model,
        resolved.provider.slug,
        exc,
    )
