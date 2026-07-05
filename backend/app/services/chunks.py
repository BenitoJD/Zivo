"""Persist document chunks with pgvector embeddings."""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document, DocumentChunk

settings = get_settings()


def pgvector_literal(vec: list[float]) -> str:
    """Format an embedding vector as a pgvector string literal for `CAST(:vec AS vector)`."""
    return "[" + ",".join(str(x) for x in vec) + "]"


def _chunk_content_hash(text_value: str) -> str:
    return hashlib.sha256((text_value or "").encode("utf-8")).hexdigest()


_EXISTING_PAGE_CHUNKS_SQL = text(
    """
    SELECT text, meta->>'content_hash' AS content_hash
    FROM document_chunks
    WHERE document_id = :document_id
      AND page_start = :page
      AND page_end = :page
    ORDER BY id
    """
)


_INSERT_CHUNK_SQL = text(
    """
    INSERT INTO document_chunks (id, document_id, page_start, page_end, text, embedding, meta)
    VALUES (:id, :document_id, :page_start, :page_end, :text, CAST(:embedding AS vector), CAST(:meta AS jsonb))
    """
)

_DELETE_PAGE_CHUNKS_SQL = text(
    """
    DELETE FROM document_chunks
    WHERE document_id = :document_id
      AND page_start = :page
      AND page_end = :page
    """
)

_DELETE_OUTSIDE_PAGES_SQL = text(
    """
    DELETE FROM document_chunks
    WHERE document_id = :document_id
      AND NOT (page_start = ANY(CAST(:allowed AS int[])))
    """
)

_INDEXED_PAGES_SQL = text(
    """
    SELECT DISTINCT page_start
    FROM document_chunks
    WHERE document_id = :document_id
      AND embedding IS NOT NULL
    ORDER BY page_start
    """
)


def load_document_chunk_texts(db: Session, document_id: uuid.UUID, *, limit: int = 200) -> list[str]:
    """Return the document's chunk texts in page order (empty list if no document/chunks).

    Shared by every longform generation graph (summarize / topics / notes / cards /
    palace / quiz), which all need the same ordered chunk text feed for map-reduce.
    """
    if not db.get(Document, document_id):
        return []
    rows = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.page_start.asc())
        .limit(limit)
        .all()
    )
    return [r.text for r in rows if r.text]


def indexed_pages_for_document(db: Session, document_id: uuid.UUID) -> set[int]:
    rows = db.execute(_INDEXED_PAGES_SQL, {"document_id": str(document_id)}).scalars().all()
    return {int(p) for p in rows}


def delete_chunks_outside_pages(
    db: Session,
    document_id: uuid.UUID,
    allowed_pages: set[int],
) -> int:
    if not allowed_pages:
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        return 0
    result = db.execute(
        _DELETE_OUTSIDE_PAGES_SQL,
        {
            "document_id": str(document_id),
            "allowed": [int(p) for p in sorted(allowed_pages)],
        },
    )
    return result.rowcount or 0


def upsert_page_chunks(
    db: Session,
    document_id: uuid.UUID,
    page: int,
    chunks: list[dict],
    embeddings: list[list[float]],
) -> int:
    if chunks and embeddings:
        existing = db.execute(
            _EXISTING_PAGE_CHUNKS_SQL,
            {"document_id": str(document_id), "page": int(page)},
        ).mappings().all()
        if len(existing) == len(chunks):
            unchanged = True
            for row, chunk in zip(existing, chunks, strict=True):
                new_hash = _chunk_content_hash(chunk.get("text") or "")
                old_hash = row.get("content_hash") or _chunk_content_hash(row.get("text") or "")
                if new_hash != old_hash:
                    unchanged = False
                    break
            if unchanged:
                return len(chunks)

    db.execute(
        _DELETE_PAGE_CHUNKS_SQL,
        {"document_id": str(document_id), "page": int(page)},
    )
    rows = [
        {
            "id": str(uuid.uuid4()),
            "document_id": str(document_id),
            "page_start": chunk["page_start"],
            "page_end": chunk["page_end"],
            "text": chunk["text"],
            "embedding": pgvector_literal(embedding),
            "meta": json.dumps(
                {
                    **(chunk.get("meta") or {}),
                    "content_hash": _chunk_content_hash(chunk.get("text") or ""),
                }
            ),
        }
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    if rows:
        db.execute(_INSERT_CHUNK_SQL, rows)
    return len(rows)


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
            "embedding": pgvector_literal(embedding),
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
            meta = dict(doc.meta or {})
            meta["no_searchable_text"] = True
            doc.meta = meta
            doc.status = "ready"
            doc.index_progress = 100
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

    from app.services.question_generation import enqueue_generate_if_needed

    enqueue_generate_if_needed(db, document_id)
