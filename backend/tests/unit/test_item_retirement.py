"""Self-improving loop — retire items real learners can't get right."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.item_retirement import retire_broken_items


def test_disabled_is_a_noop() -> None:
    db = MagicMock()
    with patch("app.services.item_retirement.get_settings") as gs:
        gs.return_value.item_self_improve_enabled = False
        assert retire_broken_items(db) == 0
    db.execute.assert_not_called()


def test_unseeded_vocab_is_a_safe_noop() -> None:
    db = MagicMock()
    with (
        patch("app.services.item_retirement.get_settings") as gs,
        patch("app.services.item_retirement.concept_id", side_effect=ValueError),
    ):
        gs.return_value.item_self_improve_enabled = True
        assert retire_broken_items(db) == 0
    db.execute.assert_not_called()


def test_retires_flagged_items_and_commits() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value.all.return_value = [
        {"aid": "id-1", "n_exposure": 12, "p_correct": 0.05},
        {"aid": "id-2", "n_exposure": 20, "p_correct": 0.0},
    ]
    with (
        patch("app.services.item_retirement.get_settings") as gs,
        patch("app.services.item_retirement.concept_id", return_value="metric-id"),
    ):
        gs.return_value.item_self_improve_enabled = True
        gs.return_value.item_retire_min_exposure = 12
        gs.return_value.item_retire_max_correct_rate = 0.08
        n = retire_broken_items(db)
    assert n == 2
    db.commit.assert_called_once()


def test_no_candidates_does_not_update() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value.all.return_value = []
    with (
        patch("app.services.item_retirement.get_settings") as gs,
        patch("app.services.item_retirement.concept_id", return_value="metric-id"),
    ):
        gs.return_value.item_self_improve_enabled = True
        gs.return_value.item_retire_min_exposure = 12
        gs.return_value.item_retire_max_correct_rate = 0.08
        assert retire_broken_items(db) == 0
    db.commit.assert_not_called()
