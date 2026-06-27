"""Birth-time difficulty prior — the cold-start estimator."""

from __future__ import annotations

from app.services.item_difficulty import PRIOR_CLAMP, estimate_birth_difficulty


def test_recall_question_is_easier_than_higher_order() -> None:
    easy = estimate_birth_difficulty(
        {"question": "What is the capital of France?", "options": ["Paris", "Lyon", "Nice"]}
    )
    hard = estimate_birth_difficulty(
        {
            "question": (
                "Why does adding a catalyst speed up a reaction without being consumed, "
                "and how would removing it change the rate over time?"
            ),
            "options": ["aaaa", "bbbb", "cccc", "dddd"],
        }
    )
    assert hard > easy


def test_more_options_is_harder() -> None:
    stem = "Choose the correct statement."
    three = estimate_birth_difficulty({"question": stem, "options": ["aa", "bb", "cc"]})
    five = estimate_birth_difficulty(
        {"question": stem, "options": ["aa", "bb", "cc", "dd", "ee"]}
    )
    assert five > three


def test_giveaway_long_option_is_easier_than_homogeneous() -> None:
    stem = "Choose the correct statement."
    homogeneous = estimate_birth_difficulty(
        {"question": stem, "options": ["aaaa", "bbbb", "cccc", "dddd"]}
    )
    giveaway = estimate_birth_difficulty(
        {
            "question": stem,
            "options": ["a", "b", "this is the obviously correct much longer answer", "d"],
        }
    )
    assert homogeneous > giveaway


def test_cognitive_angle_contributes() -> None:
    base = {"question": "Choose the statement.", "options": ["aa", "bb", "cc", "dd"]}
    analytical = estimate_birth_difficulty({**base, "cognitive_angle": "apply the principle"})
    assert analytical > estimate_birth_difficulty(base)


def test_always_clamped_to_bounds() -> None:
    extreme = estimate_birth_difficulty(
        {"question": "why how analyze apply predict " * 20, "options": ["x" * 5] * 9}
    )
    assert -PRIOR_CLAMP <= extreme <= PRIOR_CLAMP


def test_empty_or_minimal_input_is_neutral_ish() -> None:
    # No options, no verbs → a short empty stem; must not crash and stays bounded.
    v = estimate_birth_difficulty({})
    assert -PRIOR_CLAMP <= v <= PRIOR_CLAMP
