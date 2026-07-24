"""Calibration Engine — dynamic K, SE, pure update."""

from __future__ import annotations

from app.services.calibration_engine import (
    CALIBRATION_VERSION,
    DEFAULT_RATING,
    K_ITEM,
    K_LEARNER,
    dynamic_k,
    elo_update,
    expected_correct,
    se_from_info,
    update_from_outcome,
)


def test_expected_coin_flip() -> None:
    assert expected_correct(0.0, 0.0) == 0.5


def test_dynamic_k_shrinks_with_n() -> None:
    assert dynamic_k(0, K_LEARNER) > dynamic_k(10, K_LEARNER) > K_LEARNER * 0.99


def test_update_from_outcome_bumps_n_and_se() -> None:
    v = update_from_outcome(0.0, 0.0, True, ability_n=0, difficulty_n=0, running_info=0.0)
    assert v.ability > DEFAULT_RATING
    assert v.difficulty < DEFAULT_RATING
    assert v.ability_n == 1 and v.difficulty_n == 1
    assert v.ability_se > 0
    assert v.policy_version == CALIBRATION_VERSION
    assert v.k_learner > K_LEARNER  # cold boost


def test_birth_difficulty_prior_is_named_sub_policy() -> None:
    from app.services.calibration_engine import (
        BIRTH_PRIOR_POLICY,
        birth_difficulty_prior,
    )

    v = birth_difficulty_prior(
        {"question": "What is X?", "options": ["a", "b", "c", "d"]}
    )
    assert v.policy == BIRTH_PRIOR_POLICY
    assert v.policy_version == CALIBRATION_VERSION
    assert -2.0 <= v.difficulty <= 2.0


def test_se_falls_as_info_grows() -> None:
    assert se_from_info(1.0) < se_from_info(0.25)


def test_legacy_elo_update_still_works() -> None:
    upd = elo_update(0.0, 0.0, True, k_learner=K_LEARNER, k_item=K_ITEM)
    assert upd.expected == 0.5
