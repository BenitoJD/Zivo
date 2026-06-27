"""Next-question selection policies — the swappable choosing step of the loop."""

from __future__ import annotations

from app.services.selection import (
    TARGET_SUCCESS,
    LearnerState,
    _target_difficulty,
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


def test_target_difficulty_yields_the_intended_success_rate() -> None:
    # The item at _target_difficulty(ability) is one the learner should get right
    # ~TARGET_SUCCESS of the time — a desirable difficulty, below raw ability.
    from app.services.calibration import expected_correct

    ability = 1.3
    d = _target_difficulty(ability)
    assert d < ability  # the productive item sits below the learner's ability
    assert abs(expected_correct(ability, d) - TARGET_SUCCESS) < 1e-9


def test_difficulty_edge_picks_the_item_in_the_productive_band() -> None:
    ability = 1.0
    target = _target_difficulty(ability)
    # b sits in the productive band; a is far too easy, c far too hard.
    difficulties = {"a": target - 2.0, "b": target, "c": target + 2.0}
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
    # The stronger learner is served a strictly harder item than the weaker one —
    # questions climb with ability while each stays in the learner's own band.
    assert difficulties[strong] > difficulties[weak]
    assert strong == "mid" and weak == "easy"


def test_difficulty_edge_uses_neutral_prior_for_cold_items() -> None:
    # One calibrated item far from the band; a cold item ("b") is treated as sitting
    # at the learner's ability, so it wins as the closest to the target.
    out = choose_next_assertion(
        "difficulty_edge", ["a", "b"], {}, _state(0.0), {"a": 5.0}
    )
    assert out == "b"


def test_cold_first_question_with_calibrated_pool_picks_the_band_item() -> None:
    # No ability yet (first answer) but the pool is calibrated → start in the band
    # for an average learner rather than always question #1.
    target = _target_difficulty(0.0)
    difficulties = {"a": target + 2.0, "b": target, "c": target + 4.0}
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


# ---- lineage routing (react to the last answer) -----------------------------


def test_lineage_routes_a_miss_to_follow_up_after_miss() -> None:
    state = LearnerState(ability=0.0, last_correct=False, last_assertion_id="Q1")
    lineage = {"b": "follow_up_after_miss"}
    out = choose_next_assertion(
        "difficulty_edge", ["a", "b"], {}, state, {"a": 0.0, "b": 0.0}, lineage
    )
    assert out == "b"  # re-approach the missed idea


def test_lineage_routes_a_hit_to_harder_than() -> None:
    state = LearnerState(ability=0.0, last_correct=True, last_assertion_id="Q1")
    lineage = {"a": "harder_than"}
    out = choose_next_assertion(
        "difficulty_edge", ["a", "b"], {}, state, {"a": 0.0, "b": 0.0}, lineage
    )
    assert out == "a"  # advance to the harder successor


def test_lineage_ignored_when_kind_does_not_match_outcome() -> None:
    # After a hit, a miss-remediation edge is irrelevant → fall back to the band.
    state = LearnerState(ability=1.0, last_correct=True, last_assertion_id="Q1")
    target = _target_difficulty(1.0)
    difficulties = {"band": target, "remedial": target + 5.0}
    lineage = {"remedial": "follow_up_after_miss"}
    out = choose_next_assertion(
        "difficulty_edge", ["band", "remedial"], {}, state, difficulties, lineage
    )
    assert out == "band"


# ---- per-concept ability ----------------------------------------------------


def test_per_concept_ability_overrides_global_for_the_band() -> None:
    # Strong in concept A (3.0) though global ability is 0.0; both candidates are A.
    state = LearnerState(ability=0.0, concept_ability={"A": 3.0})
    concept_by_id = {"x": "A", "y": "A"}
    difficulties = {"x": _target_difficulty(3.0), "y": _target_difficulty(0.0)}
    out = choose_next_assertion(
        "difficulty_edge", ["x", "y"], concept_by_id, state, difficulties
    )
    assert out == "x"  # used per-concept ability (3.0), not global (0.0)


def test_global_ability_used_when_concept_has_no_per_concept_rating() -> None:
    state = LearnerState(ability=0.0, concept_ability={"A": 3.0})
    concept_by_id = {"x": "B", "y": "B"}  # concept B uncalibrated → global ability
    difficulties = {"x": _target_difficulty(3.0), "y": _target_difficulty(0.0)}
    out = choose_next_assertion(
        "difficulty_edge", ["x", "y"], concept_by_id, state, difficulties
    )
    assert out == "y"


def test_build_learner_state_reads_last_assertion_and_concept_ability() -> None:
    progress = {
        "learner_ability": 0.5,
        "concept_ability": {"algebra": 1.5},
        "last_confirmed_answer": {
            "assertion_id": "Q9",
            "correct": True,
            "concept_key": "algebra",
        },
    }
    state = build_learner_state(progress)
    assert state.last_assertion_id == "Q9"
    assert state.concept_ability == {"algebra": 1.5}
    assert state.last_correct is True
