"""Normalize tutor chat scope — page window around the learner's current page."""

from __future__ import annotations

from app.models import Document


def normalize_chat_scope(scope: dict | None, doc: Document | None = None) -> dict:
    """Pin retrieval to current page ± 1 when the learner is studying."""
    normalized = dict(scope or {})
    current = normalized.get("current_page")
    if current is None:
        return normalized

    page = max(1, int(current))
    normalized["current_page"] = page

    page_count = None
    if doc and doc.meta:
        raw = doc.meta.get("page_count")
        if raw is not None:
            page_count = max(1, int(raw))

    start = max(1, page - 1)
    end = page + 1
    if page_count is not None:
        end = min(page_count, end)

    normalized["page_start"] = start
    normalized["page_end"] = max(start, end)
    return normalized
