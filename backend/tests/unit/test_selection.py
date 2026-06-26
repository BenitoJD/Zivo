"""Next-question selection policies — the swappable choosing step of the loop."""

from __future__ import annotations

from app.services.selection import (
    PRODUCTIVE_STRUGGLE_MARGIN,
    LearnerState,
    build_learner_state,
    choose_next_assertion,
)


def _state(ability: float | None = None, **kw) -> LearnerState:
    return LearnerState(ability=ability, **kw)


def test_sequence_is_the_default_and_returns_first() -> None:
    out = choose_next_assertion("sequence", ["a", "b", "c"], {}, _state())
    assert out == "a"


def test_empty_candidates_returns_none() -> None:
    assert choose_next_assertion("difficulty_edge", [], {}, _state(0.0)) is None


def test_difficulty_edge_degrades_to_sequence_when_no_item_calibrated() -> None:
    # Thin data: not one candidate has a difficulty → legacy order.
    out = choose_next_assertion("difficulty_edge", ["a", "b"], {}, _state(1.0), {})
    assert out == "a"


def test_difficulty_edge_targets_ability_plus_margin() -> None:
    ability = 1.0
    target = ability + PRODUCTIVE_STRUGGLE_MARGIN  # 1.5
    # b sits exactly at the edge; a is too easy, c too hard.
    difficulties = {"a": 0.0, "b": target, "c": 4.0}
    out = choose_next_assertion(
        "difficulty_edge", ["a", "b", "c"], {}, _state(ability), difficulties
    )
    assert out == "b"


def test_strong_learner_climbs_weak_learner_bends_back() -> None:
    difficulties = {"easy": 0.0, "mid": 1.0, "hard": 2.0}
    candidates = ["easy", "mid", "hard"]
    strong = choose_next_assertion(
        "difficulty_edge", candidates, {}, _state(2.0), difficulties
    )
    weak = choose_next_assertion(
        "difficulty_edge", candidates, {}, _state(-1.0), difficulties
    )
    assert strong == "hard"  # high ability → reaches for the hardest available
    assert weak == "easy"    # low ability → meets them where they can rebuild


def test_difficulty_edge_uses_neutral_prior_for_cold_items() -> None:
    # One calibrated item far from the edge; a cold item ("b") is treated as
    # sitting at the learner's ability, so it wins as the closest to the target.
    out = choose_next_assertion(
        "difficulty_edge", ["a", "b"], {}, _state(0.0), {"a": 5.0}
    )
    assert out == "b"


def test_cold_first_question_with_calibrated_pool_picks_near_average_edge() -> None:
    # No ability yet (first answer) but the pool is calibrated → start a notch
    # above the average item rather than always question #1.
    difficulties = {"a": 0.0, "b": PRODUCTIVE_STRUGGLE_MARGIN, "c": 3.0}
    out = choose_next_assertion(
        "difficulty_edge", ["a", "b", "c"], {}, _state(None), difficulties
    )
    assert out == "b"


def test_build_learner_state_reads_ability_and_last_answer() -> None:
    progress = {
        "learner_ability": 1.25,
        "last_confirmed_answer": {"concept_key": "photosynthesis", "correct": False},
    }
    state = build_learner_state(progress)
    assert state.ability == 1.25
    assert state.last_concept_key == "photosynthesis"
    assert state.last_correct is False


def test_build_learner_state_without_calibration_has_no_ability() -> None:
    state = build_learner_state({})
    assert state.ability is None
    assert state.last_correct is None
