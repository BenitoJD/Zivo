"""Empty-page vision glance + reselect streak."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.models import Document
from app.services import rag_window as rw
from app.services.question_pool import (
    EMPTY_PAGE_RESELECT_STREAK,
    note_empty_page_triage,
    should_skip_empty_page_vision,
)
from app.services.vision import judge_page_has_content


def _doc(**meta) -> Document:
    return Document(
        id=uuid.uuid4(),
        slug="x",
        filename="x.pdf",
        content_type="application/pdf",
        size_bytes=10,
        storage_key="demo/x.pdf",
        meta=dict(meta),
    )


def test_pages_ready_includes_ingested_empty(monkeypatch) -> None:
    doc = _doc(ingested_pages=[3, 5])
    monkeypatch.setattr(rw, "indexed_pages_for_document", lambda db, doc_id: {1, 2})
    ready = rw.pages_ready_for_document(MagicMock(), doc.id, doc)
    assert ready == {1, 2, 3, 5}


def test_mark_page_ingested_records_meta() -> None:
    doc = _doc()
    db = MagicMock()
    rw.mark_page_ingested(db, doc, 4)
    assert doc.meta["ingested_pages"] == [4]
    rw.mark_page_ingested(db, doc, 2)
    assert doc.meta["ingested_pages"] == [2, 4]
    db.add.assert_called()


def test_note_empty_page_triage_asks_on_usable() -> None:
    doc = _doc()
    db = MagicMock()
    db.get.return_value = doc
    with (
        patch("app.services.question_pool.get_progress", return_value={"empty_page_streak": 0}),
        patch("app.services.question_pool.save_progress") as save,
    ):
        patch_out = note_empty_page_triage(db, doc.id, vision_usable=True)
    assert patch_out["prompt_reselect_pages"] is True
    assert patch_out["prompt_reselect_reason"] == "unreadable_content"
    save.assert_called_once()


def test_note_empty_page_triage_streak_asks_at_threshold() -> None:
    doc = _doc()
    db = MagicMock()
    db.get.return_value = doc
    with (
        patch(
            "app.services.question_pool.get_progress",
            return_value={"empty_page_streak": EMPTY_PAGE_RESELECT_STREAK - 1},
        ),
        patch("app.services.question_pool.save_progress") as save,
    ):
        patch_out = note_empty_page_triage(db, doc.id, vision_usable=False)
    assert patch_out["empty_page_streak"] == EMPTY_PAGE_RESELECT_STREAK
    assert patch_out["prompt_reselect_pages"] is True
    assert patch_out["prompt_reselect_reason"] == "empty_pages_streak"
    save.assert_called_once()


def test_note_empty_page_triage_quiet_before_threshold() -> None:
    doc = _doc()
    db = MagicMock()
    db.get.return_value = doc
    with (
        patch("app.services.question_pool.get_progress", return_value={"empty_page_streak": 1}),
        patch("app.services.question_pool.save_progress") as save,
    ):
        patch_out = note_empty_page_triage(db, doc.id, vision_usable=False)
    assert patch_out["empty_page_streak"] == 2
    assert "prompt_reselect_pages" not in patch_out
    save.assert_called_once()


def test_should_skip_empty_page_vision() -> None:
    doc = _doc()
    with patch(
        "app.services.question_pool.get_progress",
        return_value={"prompt_reselect_pages": True},
    ):
        assert should_skip_empty_page_vision(doc) is True


def test_judge_page_has_content_uses_cache() -> None:
    doc = _doc()
    db = MagicMock()
    db.get.return_value = doc
    cached = {"usable": False, "rationale": "cached blank"}
    with (
        patch("app.services.generation_cache.get", return_value=cached),
        patch("app.services.llm_sync.run_coro_in_worker") as llm,
    ):
        out = judge_page_has_content(db, doc.id, 1)
    assert out == cached
    llm.assert_not_called()


def test_judge_page_has_content_calls_vision_once() -> None:
    doc = _doc()
    db = MagicMock()
    db.get.return_value = doc

    def _run(coro):
        coro.close()
        return '{"usable": true, "rationale": "Has a labeled diagram."}'

    with (
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.generation_cache.put") as put,
        patch("app.services.vision._page_image_data_url", return_value="data:image/png;base64,xx"),
        patch("app.services.llm_registry.vision_chat_model_id", return_value=None),
        patch("app.services.llm_sync.run_coro_in_worker", side_effect=_run) as llm,
    ):
        out = judge_page_has_content(db, doc.id, 2)
    assert out["usable"] is True
    assert "diagram" in out["rationale"].lower()
    llm.assert_called_once()
    put.assert_called_once()
