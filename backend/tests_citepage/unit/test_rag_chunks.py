"""Tests for per-page chunk sync and sliding-window deletion."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.services.chunks import delete_chunks_outside_pages, upsert_page_chunks


def test_delete_chunks_outside_pages_executes() -> None:
    db = MagicMock()
    doc_id = uuid.uuid4()
    delete_chunks_outside_pages(db, doc_id, {1, 2, 3})
    db.execute.assert_called_once()


def test_upsert_page_chunks_inserts() -> None:
    db = MagicMock()
    doc_id = uuid.uuid4()
    chunks = [{"page_start": 2, "page_end": 2, "text": "page two"}]
    count = upsert_page_chunks(db, doc_id, 2, chunks, [[0.0] * 384])
    assert count == 1
    assert db.execute.call_count == 3
