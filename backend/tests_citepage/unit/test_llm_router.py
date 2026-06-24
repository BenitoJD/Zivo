"""Unit tests for LiteLLM router (DB-backed)."""

import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services import llm_router
from app.services.llm_registry import ResolvedLlmModel


def _resolved() -> ResolvedLlmModel:
    provider = LlmProvider(
        id=uuid.uuid4(),
        slug="openai",
        display_name="OpenAI",
        litellm_prefix="openai",
        api_key="test-key",
        extra_env={},
    )
    model = LlmModel(
        id=uuid.uuid4(),
        provider_id=provider.id,
        provider=provider,
        slug="test",
        litellm_model="openai/test-model",
        display_name="Test",
        kind=LlmModelKind.chat.value,
    )
    return ResolvedLlmModel(record=model, provider=provider)


def test_stream_chat_completion_uses_registry() -> None:
    db = MagicMock()
    resolved = _resolved()

    async def fake_stream():
        chunk = MagicMock()
        chunk.choices = [MagicMock(delta=MagicMock(content="hi"))]
        yield chunk

    mock_litellm = MagicMock()
    mock_litellm.acompletion = AsyncMock(return_value=fake_stream())

    async def run() -> list[str]:
        tokens: list[str] = []
        with (
            patch(
                "app.services.llm_router.iter_chat_model_attempts",
                return_value=iter([resolved]),
            ) as attempts,
            patch.dict(sys.modules, {"litellm": mock_litellm}),
        ):
            async for token in llm_router.stream_chat_completion([{"role": "user", "content": "x"}], db):
                tokens.append(token)
        attempts.assert_called_once_with(db, model_id=None, require_vision=False)
        mock_litellm.acompletion.assert_awaited_once()
        # Credentials must be passed per-call, not via os.environ.
        assert mock_litellm.acompletion.await_args.kwargs["api_key"] == "test-key"
        return tokens

    assert asyncio.run(run()) == ["hi"]
