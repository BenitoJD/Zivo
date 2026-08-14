"""confirm_page_range must not re-index a cooked background-prep doc."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_doc(*, status: str, meta: dict | None = None) -> MagicMock:
    doc = MagicMock()
    doc.id = "00000000-0000-4000-8000-000000000001"
    doc.status = status
    doc.meta = dict(meta or {})
    doc.artifact_captured_at = None
    doc.created_at = None
    return doc


def test_cooked_doc_keeps_ready_on_page_confirm() -> None:
    """A ready+prep_complete doc must not flip to indexing or re-enqueue ingest."""
    from study_api.artifacts import confirm_page_range

    doc = _make_doc(status="ready", meta={"prep_complete": True, "prep_mode": "background", "prep_phase": "complete"})
    db = MagicMock()
    db.get.return_value = doc
    body = MagicMock()
    body.pages = [1, 2]
    body.to_page = 2
    body.from_page = 1
    body.prep_mode = "now"  # default — the dangerous path

    with (
        patch("study_api.artifacts.require_document_source", return_value=doc),
        patch("study_api.artifacts.refresh_document_page_count", return_value=3),
        patch("study_api.artifacts.reset_for_new_page_range"),
        patch("study_api.artifacts.apply_prep_meta") as apply,
        patch("study_api.artifacts.workspace_repo.upsert_workspace") as upsert,
        patch("study_api.artifacts.enqueue_rag_window") as enqueue,
        patch("study_api.artifacts.enqueue_full_range_ingest") as enqueue_full,
    ):
        out = confirm_page_range("00000000-0000-4000-8000-000000000001", body, db=db, user=None, guest_id=None)

    assert out["status"] == "ready"
    assert doc.status == "ready"  # not flipped to indexing
    assert doc.index_progress == 100  # not reset to 0
    # Cooked flags restored after reset_for_new_page_range cleared them.
    assert doc.meta["prep_complete"] is True
    assert doc.meta["prep_phase"] == "complete"
    # No re-enqueue, no prep-mode reset.
    enqueue.assert_not_called()
    enqueue_full.assert_not_called()
    apply.assert_not_called()
    upsert.assert_called_once()


def test_uncooked_doc_still_reindexes() -> None:
    """A normal (not cooked) doc keeps the existing re-index behavior."""
    from study_api.artifacts import confirm_page_range

    doc = _make_doc(status="indexing", meta={"prep_mode": "background"})
    db = MagicMock()
    db.get.return_value = doc
    body = MagicMock()
    body.pages = None
    body.to_page = 2
    body.from_page = 1
    body.prep_mode = "background"

    with (
        patch("study_api.artifacts.require_document_source", return_value=doc),
        patch("study_api.artifacts.refresh_document_page_count", return_value=3),
        patch("study_api.artifacts.reset_for_new_page_range"),
        patch("study_api.artifacts.apply_prep_meta") as apply,
        patch("study_api.artifacts.workspace_repo.upsert_workspace"),
        patch("study_api.artifacts.enqueue_rag_window"),
        patch("study_api.artifacts.enqueue_full_range_ingest") as enqueue_full,
    ):
        confirm_page_range("00000000-0000-4000-8000-000000000001", body, db=db, user=None, guest_id=None)

    apply.assert_called_once()
    enqueue_full.assert_called_once()
