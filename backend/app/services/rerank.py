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

_RERANK_MODEL = "BAAI/bge-reranker-base"


@lru_cache
def get_reranker() -> TextCrossEncoder:
    return TextCrossEncoder(model_name=_RERANK_MODEL)


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
    try:
        reranker = get_reranker()
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
