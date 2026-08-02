"""Litellm Router-backed model pool with automatic cooldown + failover.

Replaces the hand-rolled failover loop with litellm's Router, which already
implements exactly the behavior wanted for production:

  * a default model (step-3.7-flash, or whichever the DB marks default),
  * automatic fallback to the next model on rate limits / timeouts / 5xx,
  * per-model cooldown: after ``allowed_fails`` failures a model is skipped
    for ``cooldown_time`` seconds, then re-enters rotation,
  * retries with backoff for transient errors (429/5xx/timeouts),
  * configurable via env:
      ZIVO_ROUTER_ENABLED          (default "1")
      ZIVO_ROUTER_COOLDOWN_SECONDS (default 120)
      ZIVO_ROUTER_ALLOWED_FAILS    (default 3)
      ZIVO_ROUTER_RATE_LIMIT_RETRIES (default 3)

The Router's model list is rebuilt whenever the DB model set changes (admin
toggles / env bootstrap) — the router instance is cheap to recreate and the
provider prefix cache is per-provider, so a rebuild only costs the failover
state.

The Router only handles chat completions (stream=False); streaming chat keeps
the existing per-tag path. Fallbacks are "same model group" (primary then the
rest of the enabled pool in DB order), which is what the old manual loop did.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from litellm import Router

from app.services.llm_registry import configure_litellm

logger = logging.getLogger(__name__)

ROUTER_ENABLED = os.getenv("ZIVO_ROUTER_ENABLED", "1") not in {"0", "false", "False"}
COOLDOWN_SECONDS = float(os.getenv("ZIVO_ROUTER_COOLDOWN_SECONDS", "120"))
ALLOWED_FAILS = int(os.getenv("ZIVO_ROUTER_ALLOWED_FAILS", "3"))
RATE_LIMIT_RETRIES = int(os.getenv("ZIVO_ROUTER_RATE_LIMIT_RETRIES", "3"))
TIMEOUT_RETRIES = int(os.getenv("ZIVO_ROUTER_TIMEOUT_RETRIES", "2"))


def router_model_list(models: list[Any]) -> list[dict]:
    """Build litellm Router ``model_list`` from the app's ResolvedLlmModel rows.

    Each deployment carries the provider credentials + model-level kwargs the
    manual path used (api_key/api_base/max_tokens/temperature/thinking flags).
    """
    out: list[dict] = []
    for m in models:
        litellm_model = m.litellm_model
        params = dict(configure_litellm(m.provider))
        if m.provider.api_key:
            params["api_key"] = m.provider.api_key
        if m.provider.api_base_url:
            params["api_base"] = str(m.provider.api_base_url).rstrip("/")
        meta = m.record.meta or {}
        if isinstance(meta, dict):
            if meta.get("max_tokens"):
                params["max_tokens"] = int(meta["max_tokens"])
            if meta.get("temperature") is not None:
                params["temperature"] = float(meta["temperature"])
        out.append(
            {
                "model_name": litellm_model,
                "litellm_params": {"model": litellm_model, **params},
            }
        )
    return out


def build_router(models: list[Any]) -> Router:
    """Construct a Router over the given pool with cooldown + retry policy."""
    model_list = router_model_list(models)
    if not model_list:
        raise ValueError("No chat models in pool for router")
    return Router(
        model_list=model_list,
        # Cooldown: after ALLOWED_FAILS failures, skip a model for this long.
        cooldown_time=COOLDOWN_SECONDS,
        allowed_fails=ALLOWED_FAILS,
        # Transient failures retried with backoff before failing over.
        retry_policy={
            "RateLimitErrorRetries": RATE_LIMIT_RETRIES,
            "TimeoutErrorRetries": TIMEOUT_RETRIES,
            "ServiceUnavailableErrorRetries": 2,
            "InternalServerErrorRetries": 1,
        },
        num_retries=0,  # Router-level retry policy above governs; avoid double-retry.
        # Fallbacks = remaining models in the list (failover chain). Router tries
        # them in order when the primary fails and cools it down.
        fallbacks=[
            {"backup": [m["model_name"] for m in model_list[1:]]}
        ],
        routing_strategy="usage-based-routing-v2",
        enable_pre_call_checks=False,
    )


def router_enabled() -> bool:
    return ROUTER_ENABLED
