"""Unit tests for document purge service."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.document_purge import purge_document


def test_purge_document_retracts_assertions_and_deletes_rows() -> None:
    doc_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.artifact_id = artifact_id
    doc.storage_key = "users/demo/file.pdf"

    assertion_id = uuid.uuid4()
    db = MagicMock()

    def execute_side_effect(statement, params=None):
        sql = str(statement)
        mock = MagicMock()
        if "SELECT id FROM intel.assertion" in sql:
            mock.all.return_value = [(assertion_id,)]
        return mock

    db.execute.side_effect = execute_side_effect

    with patch.object(db, "query") as query_mock:
        query_mock.return_value.filter.return_value.delete.return_value = 0
        storage_key = purge_document(db, doc)

    assert storage_key == "users/demo/file.pdf"
    db.delete.assert_called_once_with(doc)
    # assertion select + evidence + participant + lineage + match + retract update
    # + artifact_workspace + at least one execute for assertions
    assert db.execute.call_count >= 6
