"""Admin enable/disable for LLM chat models."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.llm_pool import is_llm_pool_enabled, set_llm_pool_enabled
from app.services.llm_registry import set_chat_model_enabled


def _chat_row(*, slug: str, enabled: bool = True, is_default: bool = False) -> LlmModel:
    provider = LlmProvider(
        id=uuid.uuid4(),
        slug="openai",
        display_name="OpenAI",
        litellm_prefix="openai",
        api_key="secret",
        is_enabled=True,
    )
    return LlmModel(
        id=uuid.uuid4(),
        provider_id=provider.id,
        provider=provider,
        slug=slug,
        litellm_model=f"openai/{slug}",
        display_name=slug,
        kind=LlmModelKind.chat.value,
        is_enabled=enabled,
        is_default=is_default,
        sort_order=0,
    )


def test_set_chat_model_enabled_clears_default_when_disabled() -> None:
    primary = _chat_row(slug="a", is_default=True)
    backup = _chat_row(slug="b")
    db = Mock()
    query = Mock()
    query.options.return_value = query
    query.filter.return_value = query
    query.first.side_effect = [primary, backup]
    enabled_query = Mock()
    enabled_query.filter.return_value = enabled_query
    enabled_query.order_by.return_value = enabled_query
    enabled_query.first.return_value = backup
    db.query.return_value = query

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.services.llm_registry._enabled_chat_query",
            lambda _db: enabled_query,
        )
        mp.setattr("app.services.llm_registry._clear_defaults_for_kind", lambda *_a, **_k: None)
        result = set_chat_model_enabled(db, primary.id, enabled=False)

    assert result.is_enabled is False
    assert primary.is_default is False
    assert backup.is_default is True
    db.commit.assert_called_once()


def test_set_chat_model_enabled_missing_raises() -> None:
    db = Mock()
    query = Mock()
    query.options.return_value = query
    query.filter.return_value = query
    query.first.return_value = None
    db.query.return_value = query

    with pytest.raises(HTTPException) as exc:
        set_chat_model_enabled(db, uuid.uuid4(), enabled=False)
    assert exc.value.status_code == 404


def test_llm_pool_runtime_override() -> None:
    set_llm_pool_enabled(False)
    try:
        assert is_llm_pool_enabled() is False
        set_llm_pool_enabled(True)
        assert is_llm_pool_enabled() is True
    finally:
        set_llm_pool_enabled(True)
