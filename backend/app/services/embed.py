import os
import threading
from functools import lru_cache

from fastembed import TextEmbedding

from app.config import get_settings
from app.engine_runtime import choose, pick

settings = get_settings()

# Smallest dense embedder in FastEmbed TextEmbedding.list_supported_models() (~0.067 GB).
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

_active_embed_model: str | None = None
# Two separate pools so a large ingest (hundreds of chunks) can't starve the
# interactive path. Ingest is batch and can tolerate waiting; query serves
# chat/generation and needs low latency. The query pool stays small because
# single-text embeds are cheap and we don't want to oversubscribe the ONNX
# runtime, which serializes across threads anyway.
_EMBED_INGEST_CONCURRENCY = int(os.getenv("ZIVO_EMBED_INGEST_CONCURRENCY", "2"))
_EMBED_QUERY_CONCURRENCY = int(os.getenv("ZIVO_EMBED_QUERY_CONCURRENCY", "2"))
_embed_ingest_semaphore = threading.Semaphore(_EMBED_INGEST_CONCURRENCY)
_embed_query_semaphore = threading.Semaphore(_EMBED_QUERY_CONCURRENCY)


def clear_query_cache() -> None:
    """Drop the embedding query cache (used when the active model changes)."""
    _embed_query_cached.cache_clear()


def set_active_embed_model(model_name: str) -> None:
    global _active_embed_model

    def _switch() -> None:
        global _active_embed_model
        _active_embed_model = model_name
        get_embedder.cache_clear()
        clear_query_cache()

    pick(_active_embed_model != model_name, _switch, lambda: None)


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
    def _embed() -> list[list[float]]:
        semaphore = choose(len(texts) >= 8, _embed_ingest_semaphore, _embed_query_semaphore)
        with semaphore:
            embedder = get_embedder()
            parallel = choose(len(texts) <= 1, 1, _parallel_workers())
            return [vec.tolist() for vec in embedder.embed(texts, parallel=parallel)]

    return pick(not texts, lambda: [], _embed)


# Bounded query cache: chat messages tend to repeat (greetings, follow-ups
# like "explain more"). 256 entries is enough for a busy session and
# bounds memory to roughly 256 * 384 * 8 bytes ~ 800 KB at the default
# embed dimension.
@lru_cache(maxsize=256)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    vec = embed_texts([text])[0]
    return tuple(float(x) for x in vec)


def embed_query(text: str) -> list[float]:
    # Match passage: prefix used at index time so query vectors share the same space.
    prefixed = choose(bool(text) and not text.startswith("query:"), f"query: {text}", text)
    return list(_embed_query_cached(prefixed))
