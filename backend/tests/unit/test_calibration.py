"""Online-Elo calibration math — the swappable metric behind the selection loop."""

from __future__ import annotations

from app.services.calibration import (
    DEFAULT_RATING,
    K_ITEM,
    K_LEARNER,
    elo_update,
    expected_correct,
)


def test_equal_ratings_are_a_coin_flip() -> None:
    assert expected_correct(0.0, 0.0) == 0.5


def test_expected_is_monotonic_in_the_gap() -> None:
    # Stronger relative to the item → more likely correct.
    assert expected_correct(2.0, 0.0) > expected_correct(0.5, 0.0) > 0.5
    assert expected_correct(-2.0, 0.0) < 0.5


def test_correct_answer_raises_ability_and_eases_item() -> None:
    upd = elo_update(DEFAULT_RATING, DEFAULT_RATING, correct=True)
    # On equal ratings the expected score is 0.5, so the steps are K*(1-0.5).
    assert upd.expected == 0.5
    assert upd.ability == DEFAULT_RATING + K_LEARNER * 0.5
    assert upd.difficulty == DEFAULT_RATING - K_ITEM * 0.5
    assert upd.ability > DEFAULT_RATING > upd.difficulty


def test_miss_lowers_ability_and_hardens_item() -> None:
    upd = elo_update(DEFAULT_RATING, DEFAULT_RATING, correct=False)
    assert upd.ability == DEFAULT_RATING - K_LEARNER * 0.5
    assert upd.difficulty == DEFAULT_RATING + K_ITEM * 0.5
    assert upd.ability < DEFAULT_RATING < upd.difficulty


def test_beating_a_hard_item_moves_ability_more_than_an_easy_win() -> None:
    # Surprising success (hard item) should shift ability more than an expected one.
    surprise = elo_update(0.0, 3.0, correct=True).ability - 0.0
    expected_win = elo_update(0.0, -3.0, correct=True).ability - 0.0
    assert surprise > expected_win > 0.0


def test_items_move_slower_than_learners() -> None:
    upd = elo_update(0.0, 0.0, correct=True)
    # Same surprise, but the item's step is damped relative to the learner's.
    assert abs(upd.difficulty - 0.0) < abs(upd.ability - 0.0)
