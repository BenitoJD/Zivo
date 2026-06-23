import os
from functools import lru_cache

from fastembed import TextEmbedding

from app.config import get_settings

settings = get_settings()

# Smallest dense embedder in FastEmbed TextEmbedding.list_supported_models() (~0.067 GB).
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

_active_embed_model: str | None = None


def clear_query_cache() -> None:
    """Drop the embedding query cache (used when the active model changes)."""
    _embed_query_cached.cache_clear()


def set_active_embed_model(model_name: str) -> None:
    global _active_embed_model
    if _active_embed_model != model_name:
        _active_embed_model = model_name
        get_embedder.cache_clear()
        clear_query_cache()


def active_embed_model() -> str:
    return _active_embed_model or settings.embed_model


@lru_cache
def get_embedder() -> TextEmbedding:
    return TextEmbedding(model_name=active_embed_model())


def _parallel_workers() -> int | None:
    """Data-parallel embedding worker count.

    fastembed's `embed(parallel=N)` spawns N ONNX workers for offline
    batch encoding of large datasets. We cap at 8 to avoid oversubscribing
    small boxes; for the single-query path embed_query() bypasses this
    (one text, no benefit).
    """
    count = os.cpu_count() or 4
    return max(1, min(count, 8))


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    embedder = get_embedder()
    # Single text -> skip data-parallel overhead; batches benefit from it.
    parallel = 1 if len(texts) <= 1 else _parallel_workers()
    return [vec.tolist() for vec in embedder.embed(texts, parallel=parallel)]


# Bounded query cache: chat messages tend to repeat (greetings, follow-ups
# like "explain more"). 256 entries is enough for a busy session and
# bounds memory to roughly 256 * 384 * 8 bytes ~ 800 KB at the default
# embed dimension.
@lru_cache(maxsize=256)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    vec = embed_texts([text])[0]
    return tuple(float(x) for x in vec)


def embed_query(text: str) -> list[float]:
    return list(_embed_query_cached(text))
