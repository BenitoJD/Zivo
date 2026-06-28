"""Best-of-N selection + the draft-model lever (selection over generation)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.config import Settings
from app.services.llm_registry import draft_chat_model_id
from app.services.mcq_quality import _draft_score, _select_best_draft


def _clean() -> dict:
    return {
        "question": "Why does a catalyst speed up a reaction?",
        "options": [
            "It lowers the activation energy",
            "It raises the temperature",
            "It adds more reactant mass",
            "It removes the product faster",
        ],
        "correct_index": 0,
    }


def _flawed() -> dict:
    # "None of the above" is a fatal heuristic flaw.
    return {
        "question": "Which statement is true?",
        "options": ["A", "B", "None of the above"],
        "correct_index": 2,
    }


def test_clean_draft_scores_better_than_flawed() -> None:
    assert _draft_score(_clean()) < _draft_score(_flawed())


def test_select_best_picks_the_clean_candidate() -> None:
    best = _select_best_draft([_flawed(), _clean()])
    assert best is not None and best["question"].startswith("Why does a catalyst")


def test_select_best_of_empty_is_none() -> None:
    assert _select_best_draft([]) is None


def test_draft_model_falls_back_when_unset() -> None:
    # No draft_model_name configured -> None, so callers use the default model.
    assert draft_chat_model_id(MagicMock()) is None


def test_world_class_defaults() -> None:
    s = Settings()
    assert s.mcq_candidates_per_aspect == 1  # best-of-N off by default
    assert s.draft_model_name == ""
    assert s.item_self_improve_enabled is True
