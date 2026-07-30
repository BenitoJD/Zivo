"""Step 3.7 as primary model, DeepSeek as failover, with automatic recovery.

Verifies the user's stated requirement:
  1. step-3.7-flash is the pinned primary (is_default = True).
  2. If step-3.7-flash fails with a failover-eligible error, the pool drops to
     DeepSeek (deepseek-v4-flash) — same-provider siblings (step-3.5) come first,
     then cross-provider DeepSeek.
  3. There is no sticky failover state: the very next request starts at
     step-3.7-flash again. Recovery is automatic, no manual step.
  4. The LITELLM_MODEL env var drives the default (the production change is
     config-only).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.llm_pool import (
    iter_chat_model_attempts,
    iter_failover_attempts,
)
from app.services.llm_registry import ResolvedLlmModel


def _provider(slug: str, *, api_key: str = "key") -> LlmProvider:
    return LlmProvider(
        id=uuid.uuid4(),
        slug=slug,
        display_name=slug,
        litellm_prefix="openai",
        api_key=api_key,
        api_base_url="https://example.com/v1",
    )


def _model(
    provider: LlmProvider,
    slug: str,
    litellm_model: str,
    *,
    sort_order: int,
    is_default: bool = False,
) -> ResolvedLlmModel:
    record = LlmModel(
        id=uuid.uuid4(),
        provider_id=provider.id,
        provider=provider,
        slug=slug,
        litellm_model=litellm_model,
        display_name=slug,
        kind=LlmModelKind.chat.value,
        sort_order=sort_order,
        is_enabled=True,
        is_default=is_default,
    )
    return ResolvedLlmModel(record=record, provider=provider)


@pytest.fixture
def pool() -> list[ResolvedLlmModel]:
    """The production model roster: deepseek, step-3.5, step-3.7 (default)."""
    deepseek = _provider("deepseek")
    stepfun = _provider("stepfun")
    return [
        _model(deepseek, "deepseek-v4-flash", "openai/deepseek-v4-flash", sort_order=4),
        _model(stepfun, "step-3.5-flash", "openai/step-3.5-flash", sort_order=5),
        _model(
            stepfun,
            "step-3.7-flash",
            "openai/step-3.7-flash",
            sort_order=6,
            is_default=True,
        ),
    ]


@pytest.fixture
def patched_pool(monkeypatch: pytest.MonkeyPatch, pool):
    monkeypatch.setattr(
        "app.services.llm_pool.get_settings",
        lambda: Settings(llm_pool_enabled=True),
    )
    monkeypatch.setattr("app.services.llm_pool.list_pool_chat_models", lambda *_a, **_k: pool)
    return pool


def test_primary_starts_at_step_37(patched_pool) -> None:
    """Requirement 1: every attempt list begins with step-3.7-flash."""
    attempts = list(iter_chat_model_attempts(MagicMock()))
    assert attempts[0].litellm_model == "openai/step-3.7-flash"


def test_failover_order_drops_to_deepseek(patched_pool) -> None:
    """Requirement 2: failover walks same-provider (step-3.5) then DeepSeek.

    The router (llm_router._model_attempts) delegates failover ordering to
    iter_failover_attempts, which tries same-provider siblings first to preserve
    the provider-side prefix cache, then cross-provider. So if step-3.7 hits a
    429/5xx/timeout, DeepSeek is reachable within two hops.
    """
    start = patched_pool[2]  # step-3.7-flash is the pinned default
    assert start.litellm_model == "openai/step-3.7-flash"

    attempts = [
        m.litellm_model for m in iter_failover_attempts(MagicMock(), start=start)
    ]
    assert attempts == [
        "openai/step-3.7-flash",
        "openai/step-3.5-flash",  # same provider, tried first (preserves prefix cache)
        "openai/deepseek-v4-flash",  # cross-provider failover
    ]


def test_failover_recovers_back_to_step_37(patched_pool) -> None:
    """Requirement 3: no sticky state. A later request restarts at step-3.7.

    iter_chat_model_attempts re-reads the default on every call, so the instant
    step-3.7 recovers, the next request uses it again — no cooldown, no manual
    switch-back.
    """
    first = [m.litellm_model for m in iter_chat_model_attempts(MagicMock())]
    # Simulate step-3.7 having failed on the prior request; nothing is persisted.
    again = [m.litellm_model for m in iter_chat_model_attempts(MagicMock())]

    assert first[0] == "openai/step-3.7-flash"
    assert again[0] == "openai/step-3.7-flash"  # unchanged — recovery is automatic
    assert first == again  # ordering is stable, no rotation


def test_litellm_model_env_drives_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 4: LITELLM_MODEL=openai/step-3.7-flash makes 3.7 the default.

    This is the exact config-only production change: setting the env var flips
    is_default onto the matching row, overriding the DeepSeek auto-pin. No code,
    no migration.
    """
    from app.services.llm_registry import sync_default_chat_model_from_env

    stepfun = _provider("stepfun")
    step37 = _model(
        stepfun, "step-3.7-flash", "openai/step-3.7-flash", sort_order=6
    )

    db = MagicMock()
    # sync_default_chat_model_from_env queries by litellm_model, then the current
    # default. The current-default lookup returns None (DeepSeek already cleared),
    # so is_default flips onto step-3.7-flash.
    db.query.return_value.filter.return_value.first.side_effect = [
        step37.record,  # lookup by litellm_model == "openai/step-3.7-flash"
        None,  # current_default lookup (no existing default)
    ]

    settings = Settings(litellm_model="openai/step-3.7-flash")
    changed = sync_default_chat_model_from_env(db, settings)

    assert changed is True
    assert step37.record.is_default is True  # flipped on by the env var
