"""Persist document chunks with pgvector embeddings."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document, DocumentChunk

settings = get_settings()


_INSERT_CHUNK_SQL = text(
    """
    INSERT INTO document_chunks (id, document_id, page_start, page_end, text, embedding, meta)
    VALUES (:id, :document_id, :page_start, :page_end, :text, CAST(:embedding AS vector), CAST(:meta AS jsonb))
    """
)


def replace_document_chunks(
    db: Session,
    document_id: uuid.UUID,
    chunks: list[dict],
    embeddings: list[list[float]],
) -> int:
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    # Single executemany (one round-trip) instead of N per-row inserts.
    # For ~400 chunks (100 pages) this cuts the DB stage from seconds to <1s.
    rows = [
        {
            "id": str(uuid.uuid4()),
            "document_id": str(document_id),
            "page_start": chunk["page_start"],
            "page_end": chunk["page_end"],
            "text": chunk["text"],
            "embedding": "[" + ",".join(str(x) for x in embedding) + "]",
            "meta": json.dumps(chunk.get("meta") or {}),
        }
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    if rows:
        db.execute(_INSERT_CHUNK_SQL, rows)
    return len(rows)


def persist_document_index(
    db: Session,
    document_id: uuid.UUID,
    chunks: list[dict],
    embeddings: list[list[float]],
) -> int:
    """Write chunk rows and mark the document ready (single transaction)."""
    if not chunks:
        doc = db.get(Document, document_id)
        if doc:
            doc.status = "failed"
            doc.index_progress = 0
        db.commit()
        return 0

    try:
        count = replace_document_chunks(db, document_id, chunks, embeddings)
        doc = db.get(Document, document_id)
        if doc:
            doc.status = "ready"
            doc.index_progress = 100
        db.commit()
        return count
    except Exception:
        db.rollback()
        doc = db.get(Document, document_id)
        if doc:
            doc.status = "failed"
            doc.index_progress = 0
            db.commit()
        raise


def finalize_image_document(db: Session, document_id: uuid.UUID) -> None:
    """Mark an uploaded image ready without text embedding."""
    doc = db.get(Document, document_id)
    if not doc:
        return
    meta = dict(doc.meta or {})
    meta["is_image"] = True
    doc.meta = meta
    doc.status = "ready"
    doc.index_progress = 100
    db.commit()
