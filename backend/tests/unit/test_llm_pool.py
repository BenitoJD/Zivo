"""Chat model pool routing and failover."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.llm_pool import (
    is_failover_eligible,
    iter_chat_model_attempts,
    list_pool_chat_models,
    litellm_provider_kwargs,
)
from app.services.llm_registry import ResolvedLlmModel


def _chat_model(
    *,
    slug: str,
    litellm_model: str,
    provider_slug: str = "openai",
    api_key: str = "key",
    sort_order: int = 0,
) -> ResolvedLlmModel:
    provider = LlmProvider(
        id=uuid.uuid4(),
        slug=provider_slug,
        display_name=provider_slug,
        litellm_prefix="openai",
        api_key=api_key,
        api_base_url="https://example.com/v1",
    )
    model = LlmModel(
        id=uuid.uuid4(),
        provider_id=provider.id,
        provider=provider,
        slug=slug,
        litellm_model=litellm_model,
        display_name=slug,
        kind=LlmModelKind.chat.value,
        sort_order=sort_order,
        is_enabled=True,
    )
    return ResolvedLlmModel(record=model, provider=provider)


def test_litellm_provider_kwargs_passes_credentials() -> None:
    resolved = _chat_model(slug="glm-4.7", litellm_model="openai/glm-4.7", provider_slug="zai")
    assert litellm_provider_kwargs(resolved) == {
        "api_key": "key",
        "api_base": "https://example.com/v1",
    }


def test_is_failover_eligible_detects_rate_limits() -> None:
    class FakeRateLimit(Exception):
        pass

    assert is_failover_eligible(FakeRateLimit("rate limit exceeded")) is True
    assert is_failover_eligible(RuntimeError("validation error")) is False


def test_iter_chat_model_attempts_round_robin(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = [
        _chat_model(slug="mimo", litellm_model="openai/mimo-v2.5", sort_order=10),
        _chat_model(
            slug="free",
            litellm_model="openrouter/openrouter/free",
            provider_slug="openrouter",
            sort_order=12,
        ),
        _chat_model(slug="glm", litellm_model="openai/glm-4.7", provider_slug="zai", sort_order=15),
    ]
    db = MagicMock()
    monkeypatch.setattr("app.services.llm_pool.get_settings", lambda: Settings(llm_pool_enabled=True))
    monkeypatch.setattr("app.services.llm_pool.list_pool_chat_models", lambda *_a, **_k: pool)

    first = [m.litellm_model for m in iter_chat_model_attempts(db)]
    second = [m.litellm_model for m in iter_chat_model_attempts(db)]

    assert first == [
        "openai/mimo-v2.5",
        "openrouter/openrouter/free",
        "openai/glm-4.7",
    ]
    assert second == [
        "openrouter/openrouter/free",
        "openai/glm-4.7",
        "openai/mimo-v2.5",
    ]


def test_iter_chat_model_attempts_empty_pool_raises() -> None:
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        list(iter_chat_model_attempts(db))
    assert exc.value.status_code == 503


def test_list_pool_chat_models_skips_missing_keys() -> None:
    provider = LlmProvider(
        id=uuid.uuid4(),
        slug="zai",
        display_name="Z.AI",
        litellm_prefix="openai",
        api_key=None,
    )
    model = LlmModel(
        id=uuid.uuid4(),
        provider_id=provider.id,
        provider=provider,
        slug="glm-4.7",
        litellm_model="openai/glm-4.7",
        display_name="GLM 4.7",
        kind=LlmModelKind.chat.value,
        is_enabled=True,
    )
    db = MagicMock()
    db.query.return_value.join.return_value.options.return_value.filter.return_value.order_by.return_value.all.return_value = [
        model
    ]
    assert list_pool_chat_models(db) == []
