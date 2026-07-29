"""Per-learner overlay coverage for shared documents."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.models import Document
from app.services.document_learner_state import document_uses_learner_overlay
from app.services.question_pool import get_progress, save_progress


def _shared_doc(**meta) -> Document:
    doc = Document(
        slug="demo",
        filename="demo.pdf",
        content_type="application/pdf",
        size_bytes=1,
        storage_key="k",
        status="ready",
        account_id=None,
        meta={"is_demo": True, "selected_range": {"from": 1, "to": 3}, **meta},
    )
    doc.id = uuid.uuid4()
    return doc


def test_document_uses_learner_overlay_for_demo_and_newspaper() -> None:
    assert document_uses_learner_overlay(_shared_doc()) is True
    assert document_uses_learner_overlay(_shared_doc(is_demo=False, is_public=True)) is True
    assert document_uses_learner_overlay(_shared_doc(newspaper=True, is_demo=False)) is True
    owned = _shared_doc()
    owned.account_id = uuid.uuid4()
    owned.meta = {"selected_range": {"from": 1, "to": 1}}
    assert document_uses_learner_overlay(owned) is False


def test_shared_doc_current_page_is_per_learner() -> None:
    doc = _shared_doc()
    db = MagicMock()

    with (
        patch("app.services.question_pool.Session.object_session", return_value=db),
        patch("app.services.question_pool._load_shared_progress", return_value={"current_page": 9}),
        patch(
            "app.services.question_pool.load_learner_progress",
            side_effect=[None, {"current_page": 2, "learn_answered_ids": ["a"]}],
        ),
    ):
        first = get_progress(doc, learner_key="user:1")
        second = get_progress(doc, learner_key="user:2")

    assert first["current_page"] == 1
    assert second["current_page"] == 2
    assert first["answered_ids"] == []
    assert second["answered_ids"] == ["a"]


def test_save_progress_routes_current_page_to_learner_overlay() -> None:
    doc = _shared_doc()
    db = MagicMock()

    with (
        patch("app.services.question_pool.get_progress", return_value={"current_page": 1}),
        patch("app.services.question_pool.save_progress_row") as save_row,
        patch("app.services.question_pool.save_learner_progress_row") as save_learner,
    ):
        save_progress(db, doc, {"current_page": 2}, learner_key="guest:abc")

    save_row.assert_not_called()
    save_learner.assert_called_once()
    assert save_learner.call_args[0][3]["current_page"] == 2
