"""Normalize tutor chat scope — sliding RAG window around the learner's current page."""

from __future__ import annotations

from app.engine_runtime import pick
from app.models import Document
from app.services.newspaper import is_newspaper_document
from app.services.question_pool import selected_page_list
from app.services.rag_window import chat_rag_window


def _pin_single_page(normalized: dict, page: int) -> None:
    normalized["page_start"] = page
    normalized["page_end"] = page
    normalized["rag_window_pages"] = [page]


def _pin_window(normalized: dict, doc: Document, page: int) -> None:
    study = selected_page_list(doc)
    window = chat_rag_window(page, study)
    normalized["page_start"] = window[0]
    normalized["page_end"] = window[-1]
    normalized["rag_window_pages"] = window


def _apply_doc(normalized: dict, doc: Document, page: int) -> None:
    pick(
        is_newspaper_document(doc),
        lambda: _pin_single_page(normalized, page),
        lambda: _pin_window(normalized, doc, page),
    )


def normalize_chat_scope(scope: dict | None, doc: Document | None = None) -> dict:
    """Pin retrieval to the active chat RAG window when the learner is studying."""
    normalized = dict(scope or {})
    current = normalized.get("current_page")

    def _with_page() -> dict:
        page = max(1, int(current))
        normalized["current_page"] = page
        pick(doc is not None, lambda: _apply_doc(normalized, doc, page), lambda: None)
        return normalized

    return pick(current is None, lambda: normalized, _with_page)
