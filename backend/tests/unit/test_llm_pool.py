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
    is_hard_provider_error,
    is_provider_benched,
    iter_chat_model_attempts,
    list_pool_chat_models,
    litellm_provider_kwargs,
    record_failover,
    reset_provider_breakers,
)
from app.services.llm_registry import ResolvedLlmModel


@pytest.fixture(autouse=True)
def _clear_breakers() -> None:
    reset_provider_breakers()


def _db_returning(*models: LlmModel) -> MagicMock:
    """A db whose pool query yields ``models`` (query→join→options→filter→order_by→filter→all)."""
    db = MagicMock()
    chain = db.query.return_value.join.return_value.options.return_value
    chain.filter.return_value.order_by.return_value.filter.return_value.all.return_value = list(models)
    return db


def _chat_model(
    *,
    slug: str,
    litellm_model: str,
    provider_slug: str = "openai",
    api_key: str = "key",
    sort_order: int = 0,
    is_default: bool = False,
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
        is_default=is_default,
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


def test_iter_chat_model_attempts_default_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pinned default takes every call (cache-hot); the pool is failover only."""
    pool = [
        _chat_model(slug="mimo", litellm_model="openai/mimo-v2.5", sort_order=10),
        _chat_model(
            slug="free",
            litellm_model="openrouter/openrouter/free",
            provider_slug="openrouter",
            sort_order=12,
            is_default=True,
        ),
        _chat_model(slug="glm", litellm_model="openai/glm-4.7", provider_slug="zai", sort_order=15),
    ]
    db = MagicMock()
    monkeypatch.setattr("app.services.llm_pool.get_settings", lambda: Settings(llm_pool_enabled=True))
    monkeypatch.setattr("app.services.llm_pool.list_pool_chat_models", lambda *_a, **_k: pool)

    expected = [
        "openrouter/openrouter/free",
        "openai/glm-4.7",
        "openai/mimo-v2.5",
    ]
    first = [m.litellm_model for m in iter_chat_model_attempts(db)]
    second = [m.litellm_model for m in iter_chat_model_attempts(db)]

    assert first == expected
    assert second == expected  # stable — no rotation between calls


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
    assert list_pool_chat_models(_db_returning(model)) == []


def test_is_hard_provider_error_separates_billing_from_rate_limits() -> None:
    """Benching is for account death, not for a provider that's merely busy."""

    class Boom(Exception):
        def __init__(self, message: str, status_code: int | None = None) -> None:
            super().__init__(message)
            self.status_code = status_code

    assert is_hard_provider_error(Boom("OpenAIException - Insufficient Balance")) is True
    assert is_hard_provider_error(Boom("Insufficient account balance", 402)) is True
    assert is_hard_provider_error(Boom("token expired or incorrect", 401)) is True
    assert is_hard_provider_error(Boom("Rate limit exceeded: free-models-per-day", 429)) is True
    # A plain per-minute 429 is transient — retrying it is correct.
    assert is_hard_provider_error(Boom("rate limit exceeded, slow down", 429)) is False
    assert is_hard_provider_error(Boom("connection timed out")) is False
    # A doc-link footer must not bench a provider that is merely busy.
    assert (
        is_hard_provider_error(Boom("overloaded, see https://x.ai/docs/billing", 429)) is False
    )


def test_record_failover_benches_provider_and_drops_it_from_pool() -> None:
    """A drained provider leaves the pool, so the next job skips it entirely."""
    dead = _chat_model(slug="deepseek", litellm_model="openai/deepseek-v4-flash", provider_slug="deepseek")
    live = _chat_model(slug="step", litellm_model="openai/step-3.7-flash", provider_slug="stepfun")

    db = _db_returning(dead.record, live.record)
    assert len(list_pool_chat_models(db)) == 2

    record_failover(dead, RuntimeError("OpenAIException - Insufficient Balance"), log_tag="test")

    assert is_provider_benched(dead.provider) is True
    assert is_provider_benched(live.provider) is False
    assert [m.litellm_model for m in list_pool_chat_models(db)] == ["openai/step-3.7-flash"]


def test_record_failover_keeps_provider_on_transient_error() -> None:
    model = _chat_model(slug="step", litellm_model="openai/step-3.7-flash", provider_slug="stepfun")
    record_failover(model, RuntimeError("connection timed out"), log_tag="test")
    assert is_provider_benched(model.provider) is False


def test_all_providers_benched_fails_fast_with_cause() -> None:
    """The whole pool dead must say why, not grind out a generic 503."""
    dead = _chat_model(slug="deepseek", litellm_model="openai/deepseek-v4-flash", provider_slug="deepseek")
    db = _db_returning(dead.record)
    record_failover(dead, RuntimeError("Insufficient Balance"), log_tag="test")

    with pytest.raises(HTTPException) as exc:
        list(iter_chat_model_attempts(db))
    assert exc.value.status_code == 503
    assert "billing" in exc.value.detail.lower()
