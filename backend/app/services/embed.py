import os
import threading
from functools import lru_cache

from fastembed import TextEmbedding

from app.config import get_settings

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
    # Heuristic pool selection: large batches are ingest (bulk re-embed),
    # small lists are query/generation (interactive). Keeps a big doc's
    # embedding pass from blocking chat.
    is_ingest = len(texts) >= 8
    semaphore = _embed_ingest_semaphore if is_ingest else _embed_query_semaphore
    with semaphore:
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
    # Match passage: prefix used at index time so query vectors share the same space.
    prefixed = f"query: {text}" if text and not text.startswith("query:") else text
    return list(_embed_query_cached(prefixed))
