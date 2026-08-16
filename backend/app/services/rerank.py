"""Cross-encoder reranking for retrieved chunks.

Raw cosine top-k retrieval is noisy. We over-fetch (limit=20) then rerank with
a small cross-encoder and keep the top N. Cross-encoders beat bi-encoder
cosine on relevance (LongLLMLingua, ACL 2024), so this both cuts context tokens
sent to the LLM (~50% fewer chunks) and usually improves answer quality.
"""

from __future__ import annotations

from functools import lru_cache

from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.config import get_settings
from app.engine_runtime import pick

# Smallest model in FastEmbed TextCrossEncoder.list_supported_models() (~0.08 GB).
DEFAULT_RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


@lru_cache
def get_reranker(model_name: str) -> TextCrossEncoder:
    return TextCrossEncoder(model_name=model_name)


def _rerank(query: str, chunks: list[dict], top_n: int) -> list[dict]:
    try:
        reranker = get_reranker(get_settings().rerank_model)
        texts = [c.get("text") or "" for c in chunks]
        scores = list(reranker.rerank(query, texts))
        paired = sorted(
            zip(chunks, scores, strict=True),
            key=lambda cs: float(cs[1]),
            reverse=True,
        )
        reranked = [c for c, _ in paired[:top_n]]
        for c, s in paired[:top_n]:
            c["rerank_score"] = float(s)
        return reranked
    except Exception:
        # Reranker is an optimization, not a correctness requirement.
        return chunks[:top_n]


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
    return pick(
        not get_settings().rerank_enabled,
        lambda: chunks[:top_n],
        lambda: pick(len(chunks) <= top_n, lambda: chunks, lambda: _rerank(query, chunks, top_n)),
    )
