"""Unit tests for document chunk persistence."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.chunks import finalize_image_document, persist_document_index, replace_document_chunks


def test_replace_document_chunks_persists_rows() -> None:
    db = MagicMock()
    doc_id = uuid.uuid4()
    chunks = [{"page_start": 1, "page_end": 1, "text": "hello world"}]
    embeddings = [[0.0] * 384]

    count = replace_document_chunks(db, doc_id, chunks, embeddings)

    assert count == 1
    assert db.execute.call_count == 2  # delete + insert
    db.commit.assert_not_called()


def test_finalize_image_document_marks_ready() -> None:
    from unittest.mock import MagicMock

    db = MagicMock()
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.meta = {}
    db.get.return_value = doc

    finalize_image_document(db, doc_id)

    assert doc.status == "ready"
    assert doc.index_progress == 100
    assert doc.meta["is_image"] is True
    db.commit.assert_called_once()


def test_persist_document_index_rolls_back_before_marking_failed(monkeypatch) -> None:
    db = MagicMock()
    doc_id = uuid.uuid4()
    doc = MagicMock()
    calls: list[str] = []

    def fail_replace(*_args, **_kwargs):
        calls.append("replace")
        raise RuntimeError("insert failed")

    def rollback() -> None:
        calls.append("rollback")

    def get(*_args, **_kwargs):
        calls.append("get")
        return doc

    monkeypatch.setattr("app.services.chunks.replace_document_chunks", fail_replace)
    db.rollback.side_effect = rollback
    db.get.side_effect = get

    with pytest.raises(RuntimeError, match="insert failed"):
        persist_document_index(
            db,
            doc_id,
            chunks=[{"page_start": 1, "page_end": 1, "text": "hello"}],
            embeddings=[[0.0] * 384],
        )

    assert calls == ["replace", "rollback", "get"]
    assert doc.status == "failed"
    assert doc.index_progress == 0
    db.commit.assert_called_once()
