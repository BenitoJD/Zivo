"""Normalize tutor chat scope — sliding RAG window around the learner's current page."""

from __future__ import annotations

from app.models import Document
from app.services.newspaper import is_newspaper_document
from app.services.question_pool import selected_page_list
from app.services.rag_window import chat_rag_window


def normalize_chat_scope(scope: dict | None, doc: Document | None = None) -> dict:
    """Pin retrieval to the active chat RAG window when the learner is studying."""
    normalized = dict(scope or {})
    current = normalized.get("current_page")
    if current is None:
        return normalized

    page = max(1, int(current))
    normalized["current_page"] = page

    if doc is not None:
        if is_newspaper_document(doc):
            # Newspaper MCQs span the edition — pin retrieval to the page that
            # cooked the active question, not a multi-page sliding window.
            normalized["page_start"] = page
            normalized["page_end"] = page
            normalized["rag_window_pages"] = [page]
        else:
            study = selected_page_list(doc)
            window = chat_rag_window(page, study)
            normalized["page_start"] = window[0]
            normalized["page_end"] = window[-1]
            normalized["rag_window_pages"] = window

    return normalized
