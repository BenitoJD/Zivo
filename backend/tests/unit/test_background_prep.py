"""Unit tests for background prep orchestration."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch


from app.models import Document
from app.services.background_prep import (
    PREP_MODE_BACKGROUND,
    apply_prep_meta,
    clear_prep_meta,
    compute_prep_progress,
    is_background_prep,
    is_prep_complete,
    page_learn_prep_ready,
)


def _doc(**meta) -> Document:
    doc = Document(
        id=uuid.uuid4(),
        filename="notes.pdf",
        content_type="application/pdf",
        status="indexing",
        index_progress=0,
        meta={
            "selected_range": {"from": 1, "to": 2, "pages": [1, 2]},
            "prep_mode": PREP_MODE_BACKGROUND,
            "prep_phase": "indexing",
            **meta,
        },
    )
    return doc


def test_is_background_prep_true_until_complete() -> None:
    doc = _doc()
    assert is_background_prep(doc) is True
    doc.meta = {**doc.meta, "prep_complete": True}
    assert is_background_prep(doc) is False


def test_clear_prep_meta_removes_flags() -> None:
    cleaned = clear_prep_meta(
        {"prep_mode": PREP_MODE_BACKGROUND, "prep_phase": "cooking", "prep_complete": False, "x": 1}
    )
    assert "prep_mode" not in cleaned
    assert "prep_phase" not in cleaned
    assert cleaned["x"] == 1


def test_apply_prep_meta_sets_background_phase() -> None:
    doc = _doc(prep_mode="now")
    db = MagicMock()
    apply_prep_meta(db, doc, prep_mode=PREP_MODE_BACKGROUND)
    assert doc.meta["prep_mode"] == PREP_MODE_BACKGROUND
    assert doc.meta["prep_phase"] == "indexing"
    assert "prep_complete" not in doc.meta


def test_page_learn_prep_ready_non_content() -> None:
    doc = _doc()
    with patch("app.services.background_prep.get_page_coverage", return_value={"non_content": True}):
        assert page_learn_prep_ready(MagicMock(), doc, 1) is True


def test_compute_prep_progress_indexing_phase() -> None:
    doc = _doc(prep_phase="indexing")
    db = MagicMock()
    with patch("app.services.background_prep.rag_window_index_progress", return_value=50):
        with patch("app.services.background_prep.page_learn_prep_ready", return_value=False):
            progress = compute_prep_progress(db, doc)
    assert progress["phase"] == "indexing"
    assert progress["index_pct"] == 50
    assert progress["overall_pct"] == 50


def test_is_prep_complete_requires_all_pages() -> None:
    doc = _doc()
    db = MagicMock()
    with patch("app.services.background_prep.all_study_pages_indexed", return_value=False):
        assert is_prep_complete(db, doc) is False
    with patch("app.services.background_prep.all_study_pages_indexed", return_value=True):
        with patch("app.services.background_prep.page_learn_prep_ready", side_effect=[True, False]):
            assert is_prep_complete(db, doc) is False
    with patch("app.services.background_prep.all_study_pages_indexed", return_value=True):
        with patch("app.services.background_prep.page_learn_prep_ready", return_value=True):
            assert is_prep_complete(db, doc) is True
