"""Page-aware text chunking for RAG."""

from __future__ import annotations

from app.services.tutor_retrieval import plan_rag_chunk_split


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
    for page in pages:
        page_num = int(page["page"])
        text = (page.get("text") or "").strip()
        if not text:
            continue
        if len(text) <= max_chars:
            chunks.append({"page_start": page_num, "page_end": page_num, "text": text})
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            piece = text[start:end].strip()
            if piece:
                chunks.append({"page_start": page_num, "page_end": page_num, "text": piece})
            if end >= len(text):
                break
            start = max(0, end - overlap)
    return chunks
