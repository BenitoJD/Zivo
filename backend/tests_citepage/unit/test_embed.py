"""Embedding model defaults."""

from __future__ import annotations

from app.config import Settings
from app.services.embed import DEFAULT_EMBED_MODEL


def test_default_embed_model_is_smallest_fastembed_dense_option() -> None:
    assert DEFAULT_EMBED_MODEL == "BAAI/bge-small-en-v1.5"
    settings = Settings()
    assert settings.embed_model == DEFAULT_EMBED_MODEL
    assert settings.embed_dimension == 384
