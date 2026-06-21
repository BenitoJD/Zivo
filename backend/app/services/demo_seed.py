"""Ensure demo document chunks have embeddings for RAG."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db import SessionLocal
from app.models import Document, DocumentChunk
from app.services.embed import embed_texts

DEMO_DOC_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def embed_demo_chunks_if_needed() -> None:
    with SessionLocal() as db:
        doc = db.get(Document, DEMO_DOC_ID)
        if not doc:
            return
        missing = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == DEMO_DOC_ID)
            .filter(text("embedding IS NULL"))
            .order_by(DocumentChunk.page_start.asc())
            .all()
        )
        if not missing:
            return
        texts = [c.text for c in missing]
        vectors = embed_texts(texts)
        for chunk, vec in zip(missing, vectors, strict=True):
            vec_literal = "[" + ",".join(str(x) for x in vec) + "]"
            db.execute(
                text("UPDATE document_chunks SET embedding = CAST(:vec AS vector) WHERE id = :id"),
                {"vec": vec_literal, "id": str(chunk.id)},
            )
        db.commit()
