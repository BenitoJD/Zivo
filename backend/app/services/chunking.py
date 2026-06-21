"""Page-aware text chunking for RAG."""

from __future__ import annotations


def chunk_pages(
    pages: list[dict],
    *,
    max_chars: int = 900,
    overlap: int = 120,
) -> list[dict]:
    """Split pages into chunks with page_start/page_end metadata."""
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
