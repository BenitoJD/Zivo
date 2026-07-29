"""Per-learner isolation for mock interview state."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.services.guest import _claim_guest_interviews
from app.services.interview import (
    load_interview,
    reset_interview,
    resolve_interview_learner_key,
)


def test_resolve_interview_learner_key() -> None:
    uid = uuid.uuid4()
    assert resolve_interview_learner_key(uid, None) == f"user:{uid}"
    assert resolve_interview_learner_key(None, "abc") == "guest:abc"
    assert resolve_interview_learner_key(None, None) == "anonymous"


def test_load_interview_filters_by_learner_key() -> None:
    doc_id = uuid.uuid4()
    db = MagicMock()
    db.execute.return_value.mappings.return_value.first.return_value = None

    result = load_interview(db, doc_id, learner_key="user:one")

    assert result["status"] == "missing"
    sql = db.execute.call_args[0][0].text
    params = db.execute.call_args[0][1]
    assert "learner_key" in sql
    assert params == {"id": doc_id, "key": "user:one"}


def test_reset_interview_deletes_only_learner_row() -> None:
    doc_id = uuid.uuid4()
    db = MagicMock()
    db.execute.return_value.mappings.return_value.first.return_value = None

    reset_interview(db, doc_id, learner_key="guest:xyz")

    delete_call = db.execute.call_args_list[0]
    sql = delete_call[0][0].text
    params = delete_call[0][1]
    assert "learner_key" in sql
    assert params == {"id": doc_id, "key": "guest:xyz"}
    db.commit.assert_called_once()


def test_claim_guest_interviews_rekeys_rows() -> None:
    db = MagicMock()
    update_result = MagicMock()
    update_result.rowcount = 2
    db.execute.side_effect = [MagicMock(), update_result]

    moved = _claim_guest_interviews(db, guest_key="guest:g1", user_key="user:u1")

    assert moved == 2
    delete_sql = db.execute.call_args_list[0][0][0].text
    update_sql = db.execute.call_args_list[1][0][0].text
    assert "DELETE FROM qb.document_interview" in delete_sql
    assert "UPDATE qb.document_interview" in update_sql
    assert db.execute.call_args_list[1][0][1] == {
        "guest_key": "guest:g1",
        "user_key": "user:u1",
    }
