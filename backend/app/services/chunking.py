"""Page-aware text chunking for RAG."""

from __future__ import annotations

from app.engine_runtime import choose, pick
from app.services.tutor_retrieval import plan_rag_chunk_split


def _split_long_page(page_num: int, text: str, max_chars: int, overlap: int) -> list[dict]:
    chunks: list[dict] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        piece = text[start:end].strip()
        chunks += choose(
            bool(piece),
            [{"page_start": page_num, "page_end": page_num, "text": piece}],
            [],
        )
        start = pick(end >= len(text), lambda: len(text), lambda: max(0, end - overlap))
    return chunks


def chunk_pages(
    pages: list[dict],
    *,
    max_chars: int | None = None,
    overlap: int | None = None,
) -> list[dict]:
    """Split pages into chunks with page_start/page_end metadata."""
    plan = plan_rag_chunk_split(max_chars=max_chars, overlap=overlap)
    max_chars = plan.max_chars
    overlap = plan.overlap
    chunks: list[dict] = []
    for page in filter(lambda p: bool((p.get("text") or "").strip()), pages):
        page_num = int(page["page"])
        text = (page.get("text") or "").strip()
        chunks += pick(
            len(text) <= max_chars,
            lambda: [{"page_start": page_num, "page_end": page_num, "text": text}],
            lambda: _split_long_page(page_num, text, max_chars, overlap),
        )
    return chunks
