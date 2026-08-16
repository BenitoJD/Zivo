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

from app.engine_runtime import choose, pick
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
    pick(not rows, lambda: h.update(b"empty"), lambda: None)
    for (ch,) in rows:
        h.update(str(ch).encode("ascii"))
        h.update(b"\x1f")
    return h.hexdigest()


def _fps(doc: Document) -> dict[str, str]:
    meta = choose(isinstance(doc.meta, dict), doc.meta, {})
    raw = meta.get(_META_KEY) or {}
    return pick(
        isinstance(raw, dict),
        lambda: {str(k): str(v) for k, v in raw.items()},
        lambda: {},
    )


def is_artifact_stale(db: Session, document_id: uuid.UUID, artifact_key: str) -> bool:
    doc = db.get(Document, document_id)
    return pick(
        not doc,
        lambda: True,
        lambda: _fps(doc).get(artifact_key) != chunks_fingerprint(db, document_id),
    )


def mark_artifact_fresh(db: Session, document_id: uuid.UUID, artifact_key: str) -> None:
    doc = db.get(Document, document_id)

    def _mark() -> None:
        live = chunks_fingerprint(db, document_id)
        meta: dict[str, Any] = dict(doc.meta or {})
        fps = dict(meta.get(_META_KEY) or {})
        fps[artifact_key] = live
        meta[_META_KEY] = fps
        doc.meta = meta
        flag_modified(doc, "meta")

    pick(not doc, lambda: None, _mark)
