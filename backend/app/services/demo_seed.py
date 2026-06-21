"""Demo document seed for try-before-upload."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Document, DocumentChunk
from app.services.embed import embed_texts

DEMO_DOC_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")

DEMO_PAGES = [
    "Executive summary: This sample report describes how municipal water systems monitor quality.",
    "Chapter 1 — Sources: Surface water and groundwater are the two primary sources for most cities.",
    "Treatment overview: Coagulation, sedimentation, filtration, and disinfection are standard stages.",
    "Testing frequency: Utilities test for bacteria daily and for metals on a quarterly schedule.",
    "Lead and copper: Homes built before 1986 may have lead service lines requiring extra monitoring.",
    "Consumer confidence reports: Utilities must publish annual water quality summaries to residents.",
    "Emergency response: Boil-water advisories are issued when contamination risk is detected.",
    "Infrastructure investment: Aging pipes increase leakage and reduce pressure in some districts.",
    "Climate impact: Drought can lower reservoir levels and concentrate certain contaminants.",
    "Conclusion: Transparent reporting and regular testing keep public water systems trustworthy.",
]


def ensure_demo_document(db: Session) -> bool:
    """Insert demo document + chunks if missing. Returns True if created."""
    if db.get(Document, DEMO_DOC_ID):
        return False
    total_size = sum(len(p) for p in DEMO_PAGES)
    doc = Document(
        id=DEMO_DOC_ID,
        account_id=None,
        slug="sample-report",
        filename="sample-report.txt",
        content_type="text/plain",
        size_bytes=total_size,
        storage_key="demo/sample-report.txt",
        status="ready",
        index_progress=100,
        meta={"is_demo": True},
    )
    db.add(doc)
    for i, page_text in enumerate(DEMO_PAGES, start=1):
        db.add(
            DocumentChunk(
                document_id=DEMO_DOC_ID,
                page_start=i,
                page_end=i,
                text=page_text,
                meta={},
            )
        )
    db.commit()
    return True


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
