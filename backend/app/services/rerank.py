"""Cross-encoder reranking for retrieved chunks.

Raw cosine top-k retrieval is noisy. We over-fetch (limit=20) then rerank with
a small cross-encoder and keep the top N. Cross-encoders beat bi-encoder
cosine on relevance (LongLLMLingua, ACL 2024), so this both cuts context tokens
sent to the LLM (~50% fewer chunks) and usually improves answer quality.

The cross-encoder pass is memoized on (query, chunk_ids) so a follow-up chat
turn that re-fetches the same chunks (common with a pinned page scope) skips
the ONNX inference entirely. Mirrors the embed_query LRU pattern in embed.py.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.config import get_settings

logger = logging.getLogger(__name__)

# Smallest model in FastEmbed TextCrossEncoder.list_supported_models() (~0.08 GB).
DEFAULT_RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"

# Cross-encoder scores are cached per (query, chunk-id tuple). 128 turns covers
# a typical chat session's follow-ups without unbounded growth.
_RERANK_CACHE_SIZE = 128


@lru_cache
def get_reranker(model_name: str) -> TextCrossEncoder:
    return TextCrossEncoder(model_name=model_name)


@lru_cache(maxsize=_RERANK_CACHE_SIZE)
def _score_chunks(query: str, chunk_ids: tuple[str, ...], texts: tuple[str, ...]) -> tuple[float, ...]:
    """Run the cross-encoder once per (query, chunk set); return scores in input order.

    Cached on the immutable (query, chunk_ids) key; ``texts`` is passed only so
    the body has the strings to score (it must match ``chunk_ids`` positionally).
    Returns a tuple so the result is hashable and cacheable.
    """
    reranker = get_reranker(get_settings().rerank_model)
    return tuple(float(s) for s in reranker.rerank(query, list(texts)))


def rerank_chunks(
    query: str,
    chunks: list[dict],
    *,
    top_n: int = 4,
) -> list[dict]:
    """Rerank chunks by query relevance, return the top_n.

    Falls back to the input order (truncated) if reranking fails or the input
    is small — never blocks retrieval on a reranker error.
    """
    if not get_settings().rerank_enabled:
        return chunks[:top_n]
    if len(chunks) <= top_n:
        return chunks
    # Stable, hashable cache key: chunk ids in input order. Falls back to the
    # text index when an id is missing (e.g. the synthetic "selection" chunk).
    chunk_ids = tuple(str(c.get("chunk_id") or f"idx-{i}") for i, c in enumerate(chunks))
    texts = tuple(c.get("text") or "" for c in chunks)
    try:
        scores = _score_chunks(query, chunk_ids, texts)
        paired = sorted(zip(chunks, scores, strict=True), key=lambda cs: cs[1], reverse=True)
        reranked = [c for c, _ in paired[:top_n]]
        for c, s in paired[:top_n]:
            c["rerank_score"] = float(s)
        return reranked
    except Exception:
        # Reranker is an optimization, not a correctness requirement.
        logger.warning("rerank_chunks failed; falling back to input order", exc_info=True)
        return chunks[:top_n]
