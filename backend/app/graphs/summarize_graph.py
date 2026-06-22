"""Whole-document summarize graph (worker-only, not chat)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt


async def generate_whole_doc_summary(db: Session, document_id: uuid.UUID) -> str:
    doc = db.get(Document, document_id)
    if not doc:
        return ""
    rows = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.page_start.asc())
        .limit(200)
        .all()
    )
    body = "\n\n".join(r.text for r in rows if r.text)
    system = get_prompt(db, "summarize_system")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Summarize this document:\n\n{body[:120000]}"},
    ]
    return await complete_chat(messages, db)
