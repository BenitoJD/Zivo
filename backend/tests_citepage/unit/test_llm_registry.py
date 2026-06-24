"""Unit tests for LLM model registry."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.llm_registry import ResolvedLlmModel, bootstrap_llm_registry_from_env


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_BASE", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _provider(**kwargs) -> LlmProvider:
    return LlmProvider(
        id=uuid.uuid4(),
        slug=kwargs.get("slug", "openai"),
        display_name=kwargs.get("display_name", "OpenAI"),
        litellm_prefix=kwargs.get("litellm_prefix", "openai"),
        api_base_url=kwargs.get("api_base_url"),
        api_key=kwargs.get("api_key", "test-key"),
        extra_env=kwargs.get("extra_env", {}),
        is_enabled=kwargs.get("is_enabled", True),
    )


def test_provider_credentials_passed_explicitly_not_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Credentials must flow through litellm_provider_kwargs (per-call) and never
    # touch os.environ, which would race between concurrent jobs on different providers.
    from app.services.llm_pool import litellm_provider_kwargs

    provider = _provider(api_base_url="https://example.com/v1/", api_key="secret")
    model = LlmModel(
        id=uuid.uuid4(),
        provider_id=provider.id,
        provider=provider,
        slug="test",
        litellm_model="openai/test-model",
        display_name="Test",
        kind=LlmModelKind.chat.value,
    )
    resolved = ResolvedLlmModel(record=model, provider=provider)

    kwargs = litellm_provider_kwargs(resolved)
    assert kwargs["api_key"] == "secret"
    assert kwargs["api_base"] == "https://example.com/v1"  # trailing slash stripped
    # Nothing leaked into os.environ.
    assert "OPENAI_API_KEY" not in __import__("os").environ
    assert "OPENAI_API_BASE" not in __import__("os").environ


def test_bootstrap_llm_registry_from_env_seeds_empty_db() -> None:
    from app.config import Settings

    db = MagicMock()
    query = MagicMock()
    query.count.return_value = 0
    query.filter.return_value.first.return_value = None
    db.query.return_value = query

    settings = Settings(
        litellm_model="openai/mimo-v2.5",
        openai_api_key="bootstrap-key",
        openai_api_base="https://example.com/v1",
    )

    bootstrap_llm_registry_from_env(db, settings)

    assert db.add.called
    db.commit.assert_called_once()
