"""Unit tests for generation job checkpoints."""

import uuid
from unittest.mock import MagicMock, patch

from app.services.generation_checkpoint import resolve_start_sequence, save_generation_checkpoint


def test_resolve_start_sequence_uses_assertion_count() -> None:
    db = MagicMock()
    document_id = uuid.uuid4()
    with patch(
        "app.services.question_pool.count_assertions_on_page",
        return_value=3,
    ), patch(
        "app.services.generation_checkpoint.eta_context.get_current_job_id",
        return_value=None,
    ):
        start = resolve_start_sequence(
            db,
            document_id,
            page_number=2,
            options={"start_sequence": 1},
        )
    assert start == 3


def test_save_generation_checkpoint_merges_result() -> None:
    db = MagicMock()
    job_id = uuid.uuid4()
    row = MagicMock()
    row.result = {"questions_saved": 1}
    db.get.return_value = row

    save_generation_checkpoint(db, job_id, page_number=4, last_sequence=7, saved_total=2)

    assert row.result["checkpoint"]["last_sequence"] == 7
    db.commit.assert_called_once()
