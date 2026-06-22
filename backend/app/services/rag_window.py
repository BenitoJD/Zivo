"""Sliding-window RAG for tutor chat — max N pages indexed at a time."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import Document
from app.services.chunks import delete_chunks_outside_pages, indexed_pages_for_document
from app.services.question_pool import get_progress, selected_page_list

MAX_RAG_PAGES = 6


def chat_rag_window(
    current_page: int,
    study_pages: list[int],
    *,
    max_pages: int = MAX_RAG_PAGES,
) -> list[int]:
    """Pages to index for chat RAG around the learner's current page."""
    if not study_pages:
        return [max(1, int(current_page))]

    study = sorted({int(p) for p in study_pages if int(p) >= 1})
    current = max(1, int(current_page))
    if current not in study:
        future = [p for p in study if p >= current]
        current = future[0] if future else study[-1]

    look_ahead = 1 if current < 3 else 2
    window_end = current + look_ahead
    candidates = [p for p in study if p <= window_end]
    if not candidates:
        return [current]
    if len(candidates) <= max_pages:
        return candidates
    return candidates[-max_pages:]


def rag_window_meta(pages: list[int]) -> dict[str, Any]:
    ordered = sorted(pages)
    return {
        "pages": ordered,
        "page_start": ordered[0],
        "page_end": ordered[-1],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_rag_window(doc: Document) -> list[int]:
    raw = (doc.meta or {}).get("rag_window") or {}
    pages = raw.get("pages")
    if isinstance(pages, list) and pages:
        return sorted({int(p) for p in pages})
    progress = get_progress(doc)
    current = int(progress.get("current_page") or 1)
    return chat_rag_window(current, selected_page_list(doc))


def save_rag_window(db: Session, doc: Document, pages: list[int]) -> None:
    meta = dict(doc.meta or {})
    meta["rag_window"] = rag_window_meta(pages)
    meta.pop("rag_window_ready", None)
    doc.meta = meta
    flag_modified(doc, "meta")


def sync_rag_window(
    db: Session,
    document_id: uuid.UUID,
    target_pages: list[int],
) -> list[int]:
    """Delete chunks outside the window; return study pages that still need ingest."""
    allowed = {int(p) for p in target_pages}
    delete_chunks_outside_pages(db, document_id, allowed)
    indexed = indexed_pages_for_document(db, document_id)
    return sorted(p for p in allowed if p not in indexed)


def rag_window_index_progress(db: Session, document_id: uuid.UUID, target_pages: list[int]) -> int:
    if not target_pages:
        return 100
    indexed = indexed_pages_for_document(db, document_id)
    done = sum(1 for p in target_pages if p in indexed)
    return int(100 * done / len(target_pages))


def is_rag_window_ready(db: Session, document_id: uuid.UUID, doc: Document | None = None) -> bool:
    if doc is None:
        doc = db.get(Document, document_id)
    if not doc:
        return False
    meta = doc.meta or {}
    if meta.get("rag_window_ready"):
        return True
    target = get_rag_window(doc)
    if not target:
        return True
    indexed = indexed_pages_for_document(db, document_id)
    return all(p in indexed for p in target)


def refresh_rag_window_status(db: Session, document_id: uuid.UUID) -> bool:
    """Update index_progress and ready flag; return True when window fully indexed."""
    doc = db.get(Document, document_id)
    if not doc:
        return False
    target = get_rag_window(doc)
    pct = rag_window_index_progress(db, document_id, target)
    doc.index_progress = pct
    ready = is_rag_window_ready(db, document_id, doc)
    meta = dict(doc.meta or {})
    if ready:
        doc.status = "ready"
        meta["rag_window_ready"] = True
    doc.meta = meta
    flag_modified(doc, "meta")
    db.commit()
    if ready:
        from app.services.question_generation import enqueue_generate_if_needed

        enqueue_generate_if_needed(db, document_id)
    return ready
