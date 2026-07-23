"""Document chunk fingerprint — invalidate cached study artifacts on re-index.

Study artifacts (notes, topics, cards, …) persist as ``status=ready`` forever.
When chunks change we compare a live fingerprint of ``document_chunks`` against
the fingerprint stored in ``doc.meta["study_artifact_fps"]`` and force regenerate.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import Document

_META_KEY = "study_artifact_fps"


def chunks_fingerprint(db: Session, document_id: uuid.UUID) -> str:
    rows = db.execute(
        text(
            """
            SELECT COALESCE(meta->>'content_hash', md5(COALESCE(text, '')))
            FROM document_chunks
            WHERE document_id = :id
            ORDER BY page_start ASC, id ASC
            """
        ),
        {"id": document_id},
    ).all()
    h = hashlib.sha256()
    if not rows:
        h.update(b"empty")
    for (ch,) in rows:
        h.update(str(ch).encode("ascii"))
        h.update(b"\x1f")
    return h.hexdigest()


def _fps(doc: Document) -> dict[str, str]:
    meta = doc.meta if isinstance(doc.meta, dict) else {}
    raw = meta.get(_META_KEY) or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def is_artifact_stale(db: Session, document_id: uuid.UUID, artifact_key: str) -> bool:
    doc = db.get(Document, document_id)
    if not doc:
        return True
    live = chunks_fingerprint(db, document_id)
    stored = _fps(doc).get(artifact_key)
    return stored != live


def mark_artifact_fresh(db: Session, document_id: uuid.UUID, artifact_key: str) -> None:
    doc = db.get(Document, document_id)
    if not doc:
        return
    live = chunks_fingerprint(db, document_id)
    meta: dict[str, Any] = dict(doc.meta or {})
    fps = dict(meta.get(_META_KEY) or {})
    fps[artifact_key] = live
    meta[_META_KEY] = fps
    doc.meta = meta
    flag_modified(doc, "meta")
