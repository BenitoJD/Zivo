"""Unit tests for ArtifactStore SQL construction (no DB)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.services.artifact_store import ArtifactStore


def test_save_includes_document_id_in_insert() -> None:
    store = ArtifactStore(table="qb.document_topics", key_cols=[], payload_col="outline")
    db = MagicMock()
    doc_id = uuid.uuid4()

    store.save(db, doc_id, [{"key": "a", "title": "A", "summary": ""}])

    sql = str(db.execute.call_args.args[0])
    params = db.execute.call_args.args[1]
    assert "document_id" in sql
    assert ":id" in sql
    assert params["id"] == doc_id
    assert "ON CONFLICT (document_id)" in sql


def test_set_status_includes_document_id_in_insert() -> None:
    store = ArtifactStore(
        table="qb.topic_explanation",
        key_cols=["topic_key"],
        payload_col="explanation",
        cast_jsonb=False,
    )
    db = MagicMock()
    doc_id = uuid.uuid4()

    store.set_status(db, doc_id, "generating", topic_key="intro")

    sql = str(db.execute.call_args.args[0])
    params = db.execute.call_args.args[1]
    assert "INSERT INTO qb.topic_explanation (document_id, topic_key," in sql
    assert params["id"] == doc_id
    assert params["topic_key"] == "intro"
    assert "ON CONFLICT (document_id, topic_key)" in sql
