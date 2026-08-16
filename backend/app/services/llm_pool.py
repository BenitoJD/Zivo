"""Default-first chat model pool — failover-only, never rotation.

One provider takes all traffic so its server-side prefix cache stays hot;
the rest of the pool exists solely for failover (same-provider siblings
first). Rotating requests across providers would destroy the prefix-cache
hit rate this design exists to protect.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.llm_registry import ResolvedLlmModel, configure_litellm
from app.services.llm_route import evaluate_llm_route
from app.services.presence import evaluate_presence

logger = logging.getLogger(__name__)

_pool_enabled_override: bool | None = None


def _raise(exc: BaseException) -> None:
    raise exc


def is_llm_pool_enabled() -> bool:
    return apply(
        evaluate_presence(_pool_enabled_override).action,
        {
            "ok": lambda: _pool_enabled_override,
            "missing": lambda: get_settings().llm_pool_enabled,
            "empty": lambda: get_settings().llm_pool_enabled,
        },
    )


def set_llm_pool_enabled(enabled: bool) -> None:
    global _pool_enabled_override
    _pool_enabled_override = enabled


def _provider_ready(provider: LlmProvider) -> bool:
    return choose(provider.slug == "local", True, bool((provider.api_key or "").strip()))


# --- Hard-failure breaker -----------------------------------------------------
#
# A drained balance or a dead key is not transient: the next call gets the same
# 402. Without a breaker every job re-probes the corpse. LiteLLM retries it
# twice, failover moves to the next model, that one is also dead, twice more,
# so a pool-wide billing outage reads as "stuck" (workers pinned, queue backing
# up) instead of "broken". Cooldown is short so restoring credit self-heals
# without a deploy.
# ponytail: in-process dict, per replica. Move to Redis only if replicas ever
# need to share the verdict; today each one learns it within one call.
_HARD_ERROR_COOLDOWN_S = float(os.getenv("ZIVO_LLM_PROVIDER_COOLDOWN_S", "300"))
_provider_down_until: dict[uuid.UUID, float] = {}

_HARD_ERROR_TOKENS = (
    "insufficient balance",
    "insufficient account balance",
    "insufficient_balance",
    "exceeded your current quota",
    "credit balance is too low",
    "invalid api key",
    "incorrect api key",
    "token expired",
    "payment required",
    # Free-tier per-day caps are hard until the daily reset, unlike a
    # per-minute rate limit which is worth retrying immediately.
    "per-day",
    "per day",
)
# Deliberately NOT matched: a bare "billing" or "quota", which show up in the
# doc-link footer of healthy providers' transient errors and would bench them.

_FAILOVER_STATUS = {401, 402, 403, 408, 409, 429, 500, 502, 503, 504}
_HARD_STATUS = {401, 402, 403}
_FAILOVER_NAME_TOKENS = ("timeout", "rate", "unavailable", "connection")
_FAILOVER_MESSAGE_TOKENS = (
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


def _exc_status(exc: BaseException) -> object:
    status = getattr(exc, "status_code", None)
    return pick(
        status is None,
        lambda: getattr(getattr(exc, "response", None), "status_code", None),
        lambda: status,
    )


def is_hard_provider_error(exc: BaseException) -> bool:
    """Account-level failure: no balance, dead key, or spent daily quota.

    Distinct from ``is_failover_eligible``: that asks "try the next model?",
    this asks "is this provider worth calling again soon?".
    """
    status = _exc_status(exc)
    message = str(exc).lower()
    return (isinstance(status, int) and status in _HARD_STATUS) or any(
        token in message for token in _HARD_ERROR_TOKENS
    )


def mark_provider_down(provider: LlmProvider, exc: BaseException) -> None:
    """Bench a provider that returned an account-level failure."""

    def _mark() -> None:
        _provider_down_until[provider.id] = time.monotonic() + _HARD_ERROR_COOLDOWN_S
        logger.error(
            "llm_provider_down provider=%s cooldown_s=%s error=%s",
            provider.slug,
            _HARD_ERROR_COOLDOWN_S,
            exc,
        )

    pick(_HARD_ERROR_COOLDOWN_S <= 0, lambda: None, _mark)


def is_provider_benched(provider: LlmProvider) -> bool:
    until = _provider_down_until.get(provider.id)

    def _check() -> bool:
        expired = time.monotonic() >= until
        pick(expired, lambda: _provider_down_until.pop(provider.id, None), lambda: None)
        return not expired

    return apply(
        evaluate_presence(until).action,
        {
            "missing": lambda: False,
            "empty": lambda: False,
            "ok": _check,
        },
    )


def reset_provider_breakers() -> None:
    """Clear all cooldowns: for tests and for an admin-triggered retry."""
    _provider_down_until.clear()


def _ready_resolved(model: LlmModel) -> ResolvedLlmModel | None:
    return pick(
        _provider_ready(model.provider) and not is_provider_benched(model.provider),
        lambda: ResolvedLlmModel(record=model, provider=model.provider),
        lambda: None,
    )


def list_pool_chat_models(
    db: Session,
    *,
    require_vision: bool = False,
) -> list[ResolvedLlmModel]:
    """Enabled chat models whose provider has credentials, ordered for routing."""
    base = (
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
    query = pick(
        require_vision,
        lambda: base.filter(LlmModel.supports_image_input.is_(True)),
        lambda: base.filter(LlmModel.vision_only.is_(False)),
    )
    return list(filter(None, map(_ready_resolved, query.all())))


def _unique_attempts(seq: tuple[ResolvedLlmModel, ...]) -> list[ResolvedLlmModel]:
    seen: set[uuid.UUID] = set()
    out: list[ResolvedLlmModel] = []
    for candidate in seq:
        mid = candidate.record.id
        pick(
            mid in seen,
            lambda: None,
            lambda m=mid, c=candidate: (seen.add(m), out.append(c)),
        )
    return out


def iter_failover_attempts(
    db: Session,
    *,
    start: ResolvedLlmModel,
    require_vision: bool = False,
) -> Iterator[ResolvedLlmModel]:
    """Yield models to try after a transient error: same provider first.

    Cross-provider failover re-bills the full uncached prefix; exhausting
    same-provider siblings first preserves provider-side prefix caches.
    """
    pool = list_pool_chat_models(db, require_vision=require_vision)

    def _from_pool() -> Iterator[ResolvedLlmModel]:
        provider_id = start.provider.id
        same_provider = tuple(filter(lambda m: m.provider.id == provider_id, pool))
        other_provider = tuple(filter(lambda m: m.provider.id != provider_id, pool))
        return iter(_unique_attempts((start, *same_provider, *other_provider)))

    yield from pick(not pool, lambda: iter((start,)), _from_pool)


def iter_chat_model_attempts(
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
) -> Iterator[ResolvedLlmModel]:
    """Yield models to try: explicit pick, or default-first with pool failover.

    Every call starts at the pinned default (falling back to pool order) instead
    of round-robining: one provider takes all traffic, so its server-side prompt
    prefix cache stays hot (DeepSeek et al. bill cache hits at a fraction of the
    uncached rate). The rest of the pool remains as failover only.
    """

    def _pinned() -> Iterator[ResolvedLlmModel]:
        from app.services.llm_registry import resolve_chat_model

        return iter((resolve_chat_model(db, model_id=model_id, require_vision=require_vision),))

    def _single() -> Iterator[ResolvedLlmModel]:
        from app.services.llm_registry import resolve_chat_model

        return iter((resolve_chat_model(db, require_vision=require_vision),))

    def _empty() -> Iterator[ResolvedLlmModel]:
        apply(
            first_match(
                (
                    Rule(when=(Pred("benched", "truthy"),), action="billing"),
                    Rule(when=(), action="none"),
                ),
                {"benched": bool(_provider_down_until)},
            ).action,
            {
                "billing": lambda: _raise(
                    HTTPException(
                        status_code=503,
                        detail=(
                            "No LLM capacity: every configured provider returned a billing or "
                            "credential failure. Check provider balances and API keys."
                        ),
                    )
                ),
                "none": lambda: _raise(
                    HTTPException(status_code=503, detail="No chat model configured")
                ),
            },
        )
        return iter(())

    def _rotate(pool: list[ResolvedLlmModel]) -> Iterator[ResolvedLlmModel]:
        start = next(
            map(
                lambda pair: pair[0],
                filter(lambda pair: pair[1].record.is_default, enumerate(pool)),
            ),
            0,
        )
        return (pool[(start + offset) % len(pool)] for offset in range(len(pool)))

    def _from_pool() -> Iterator[ResolvedLlmModel]:
        pool = list_pool_chat_models(db, require_vision=require_vision)
        return pick(not pool, _empty, lambda: _rotate(pool))

    yield from apply(
        evaluate_llm_route(
            has_primary=model_id is not None,
            has_fallback=is_llm_pool_enabled(),
        ).action,
        {
            "use_primary": _pinned,
            "use_fallback": _from_pool,
            "skip": _single,
        },
    )


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

    status = _exc_status(exc)
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        isinstance(exc, (RateLimitError, Timeout, ServiceUnavailableError, APIConnectionError))
        or (isinstance(status, int) and status in _FAILOVER_STATUS)
        or isinstance(exc, APIError)
        or any(token in name for token in _FAILOVER_NAME_TOKENS)
        or any(token in message for token in _FAILOVER_MESSAGE_TOKENS)
    )


def litellm_provider_kwargs(resolved: ResolvedLlmModel) -> dict[str, str]:
    """Per-request provider credentials + model-level call params.

    Credentials (avoids env-var races between providers). Model-level params
    are read from the model row's ``meta`` JSONB so per-model behavior (for
    example disabling a hosted model's reasoning/thinking mode or capping
    output tokens) is data-driven rather than hardcoded. Only keys litellm
    understands are forwarded; unknown keys are ignored.

    Supported ``meta`` keys:
    - ``thinking_disabled`` (bool): emit ``thinking={"type": "disabled"}`` and an
      OpenAI-compatible ``extra_body`` fallback (Z.AI GLM 4.x emits a 1-3k-token
      reasoning trace by default that we discard: disabling it is a ~5x latency win).
    - ``max_tokens`` (int): hard cap on completion tokens.
    - ``temperature`` (float): sampling temperature.
    """
    provider = resolved.provider
    params: dict = dict(configure_litellm(provider))
    pick(bool(provider.api_key), lambda: params.__setitem__("api_key", provider.api_key), lambda: None)
    pick(
        bool(provider.api_base_url),
        lambda: params.__setitem__("api_base", str(provider.api_base_url).rstrip("/")),
        lambda: None,
    )

    meta = resolved.record.meta or {}
    meta = pick(isinstance(meta, dict), lambda: meta, lambda: {})

    def _thinking() -> None:
        params["thinking"] = {"type": "disabled"}
        params.setdefault("extra_body", {})
        pick(
            isinstance(params.get("extra_body"), dict),
            lambda: (
                params["extra_body"].setdefault("chat_template_kwargs", {}),
                params["extra_body"]["chat_template_kwargs"].setdefault("enable_thinking", False),
            ),
            lambda: None,
        )

    pick(
        bool(meta.get("thinking_disabled")) and resolved.provider.slug == "zai",
        _thinking,
        lambda: None,
    )

    max_tokens = meta.get("max_tokens")
    pick(
        isinstance(max_tokens, int) and max_tokens > 0,
        lambda: params.__setitem__("max_tokens", max_tokens),
        lambda: None,
    )
    temperature = meta.get("temperature")
    pick(
        isinstance(temperature, (int, float)),
        lambda: params.__setitem__("temperature", temperature),
        lambda: None,
    )
    return params


def record_failover(resolved: ResolvedLlmModel, exc: BaseException, *, log_tag: str) -> None:
    """Log the failover and, on an account-level error, bench the provider.

    Every failover path routes through here, so this is the one place the
    breaker has to trip.
    """
    logger.warning(
        "llm_failover tag=%s model=%s provider=%s error=%s",
        log_tag,
        resolved.litellm_model,
        resolved.provider.slug,
        exc,
    )
    pick(is_hard_provider_error(exc), lambda: mark_provider_down(resolved.provider, exc), lambda: None)
