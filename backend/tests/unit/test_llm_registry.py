"""Unit tests for LLM model registry."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.models.llm import LlmProvider
from app.services.llm_registry import (
    bootstrap_llm_registry_from_env,
    configure_litellm,
)


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


def test_configure_litellm_maps_openai_provider() -> None:
    provider = _provider(api_base_url="https://example.com/v1/", api_key="secret")
    kwargs = configure_litellm(provider)
    assert kwargs["api_base"] == "https://example.com/v1"
    assert kwargs["api_key"] == "secret"


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
