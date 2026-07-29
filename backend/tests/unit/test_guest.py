"""Guest document access helpers."""

from __future__ import annotations

import uuid

from app.models import Document, User
from app.services.guest import (
    can_access_document,
    claim_guest_documents,
    claim_guest_progress,
    document_owned_by_guest,
)


def _doc(**kwargs) -> Document:
    return Document(
        id=uuid.uuid4(),
        slug="test",
        filename="t.txt",
        content_type="text/plain",
        size_bytes=1,
        storage_key="k",
        **kwargs,
    )


def test_guest_can_access_own_document() -> None:
    doc = _doc(account_id=None, meta={"guest_id": "abc"})
    assert can_access_document(doc, None, "abc")


def test_guest_cannot_access_other_guest_document() -> None:
    doc = _doc(account_id=None, meta={"guest_id": "abc"})
    assert not can_access_document(doc, None, "xyz")


def test_demo_document_is_public() -> None:
    doc = _doc(account_id=None, meta={"is_demo": True})
    assert can_access_document(doc, None, None)


def test_user_document_requires_owner() -> None:
    account_id = uuid.uuid4()
    doc = _doc(account_id=account_id, meta={})
    user = User(id=account_id, username="dev", password_hash="x")
    assert can_access_document(doc, user, None)
    assert not can_access_document(doc, None, "guest")


def test_guest_limits_config() -> None:
    from app.config import get_settings

    settings = get_settings()
    assert settings.guest_document_limit == 1
    assert settings.guest_message_limit == 30


def test_document_owned_by_guest() -> None:
    doc = _doc(account_id=None, meta={"guest_id": "g1"})
    assert document_owned_by_guest(doc, "g1")
    assert not document_owned_by_guest(doc, "g2")


def test_claim_guest_documents_transfers_ownership() -> None:
    from unittest.mock import MagicMock

    from app.models import ChatThread, Document

    account_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    guest_id = "a" * 32
    doc = _doc(account_id=None, meta={"guest_id": guest_id})
    doc.id = doc_id

    db = MagicMock()

    def query_side(model: type) -> MagicMock:
        chain = MagicMock()
        if model is Document:
            chain.filter.return_value.filter.return_value.all.return_value = [doc]
        elif model is ChatThread:
            chain.filter.return_value.filter.return_value.update.return_value = 0
        return chain

    db.query.side_effect = query_side

    claimed = claim_guest_documents(db, account_id, guest_id)

    assert claimed == [doc_id]
    assert doc.account_id == account_id
    assert "guest_id" not in doc.meta
    db.commit.assert_called_once()


def test_claim_guest_documents_skips_without_cookie() -> None:
    from unittest.mock import MagicMock

    from app.services.guest_session import read_guest_id_from_cookie

    account_id = uuid.uuid4()
    assert read_guest_id_from_cookie(None) is None

    db = MagicMock()
    claimed = claim_guest_documents(db, account_id, read_guest_id_from_cookie(None))
    assert claimed == []
    db.commit.assert_not_called()


def test_claim_guest_progress_skips_without_cookie() -> None:
    from unittest.mock import MagicMock

    db = MagicMock()
    result = claim_guest_progress(db, uuid.uuid4(), "user", None)
    assert result == {
        "learner_state_rows": 0,
        "measurements_moved": 0,
        "sd_sessions_moved": 0,
        "notes_claimed": 0,
    }
    db.commit.assert_not_called()


def test_merge_learner_progress_rows_unions_answered_ids() -> None:
    from app.services.guest import _merge_learner_progress_rows

    merged = _merge_learner_progress_rows(
        {"learn_answered_ids": ["a", "b"], "session_items_answered": 2, "mastery_stop": True},
        {"learn_answered_ids": ["b", "c"], "session_items_answered": 1, "mastery_stop": False},
    )
    assert merged["learn_answered_ids"] == ["b", "c", "a"]
    assert merged["session_items_answered"] == 2
    assert merged["mastery_stop"] is True
