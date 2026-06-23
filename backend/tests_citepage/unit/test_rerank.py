"""Reranker configuration and fallbacks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.config import Settings
from app.services.rerank import DEFAULT_RERANK_MODEL, rerank_chunks


def test_default_rerank_model_is_smallest_fastembed_option() -> None:
    assert DEFAULT_RERANK_MODEL == "Xenova/ms-marco-MiniLM-L-6-v2"
    settings = Settings()
    assert settings.rerank_model == DEFAULT_RERANK_MODEL


def test_rerank_chunks_skips_when_disabled() -> None:
    chunks = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    with patch("app.services.rerank.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(rerank_enabled=False)
        assert rerank_chunks("query", chunks, top_n=2) == chunks[:2]


def test_rerank_chunks_skips_when_already_small() -> None:
    chunks = [{"text": "only"}]
    with patch("app.services.rerank.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(rerank_enabled=True, rerank_model=DEFAULT_RERANK_MODEL)
        assert rerank_chunks("query", chunks, top_n=4) == chunks
