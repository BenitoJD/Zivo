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

from app.engine_runtime import choose, pick

logger = logging.getLogger(__name__)

ROUTER_ENABLED = os.getenv("ZIVO_ROUTER_ENABLED", "1") not in {"0", "false", "False"}
COOLDOWN_SECONDS = float(os.getenv("ZIVO_ROUTER_COOLDOWN_SECONDS", "120"))
ALLOWED_FAILS = int(os.getenv("ZIVO_ROUTER_ALLOWED_FAILS", "3"))
RATE_LIMIT_RETRIES = int(os.getenv("ZIVO_ROUTER_RATE_LIMIT_RETRIES", "3"))
TIMEOUT_RETRIES = int(os.getenv("ZIVO_ROUTER_TIMEOUT_RETRIES", "2"))


def router_model_list(models: list[Any]) -> list[dict]:
    """Build litellm Router ``model_list`` from the app's ResolvedLlmModel rows.

    Each deployment carries the SAME credentials + model-level kwargs the manual
    path computes (api_key/api_base/max_tokens/temperature/thinking flags) via
    llm_pool.litellm_provider_kwargs — the deployment, not the request, is where
    these belong so Router fallbacks use each model's own credentials.
    """
    from app.services.llm_pool import litellm_provider_kwargs

    out: list[dict] = []
    for m in models:
        litellm_model = m.litellm_model
        params = dict(litellm_provider_kwargs(m))
        out.append(
            {
                "model_name": litellm_model,
                "litellm_params": {"model": litellm_model, **params},
            }
        )
    return out


def build_router(models: list[Any]) -> Router:
    """Construct a Router over the given pool with cooldown + retry policy."""
    def _raise_empty() -> Router:
        raise ValueError("No chat models in pool for router")

    model_list = router_model_list(models)

    def _build() -> Router:
        return Router(
            model_list=model_list,
            cooldown_time=COOLDOWN_SECONDS,
            allowed_fails=ALLOWED_FAILS,
            retry_policy={
                "RateLimitErrorRetries": RATE_LIMIT_RETRIES,
                "TimeoutErrorRetries": TIMEOUT_RETRIES,
                "ServiceUnavailableErrorRetries": 2,
                "InternalServerErrorRetries": 1,
            },
            num_retries=0,
            fallbacks=choose(
                len(model_list) > 1,
                [{model_list[0]["model_name"]: [m["model_name"] for m in model_list[1:]]}],
                [],
            ),
            routing_strategy="simple-shuffle",
            enable_pre_call_checks=True,
        )

    return pick(not model_list, _raise_empty, _build)


def router_enabled() -> bool:
    return ROUTER_ENABLED
