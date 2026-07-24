"""Online calibration — compatibility re-exports for the Calibration Engine.

Canonical implementation: ``app.services.calibration_engine``
Design: docs/CALIBRATION_ENGINE.md
"""

from __future__ import annotations

from app.services.calibration_engine import (  # noqa: F401
    ABILITY_PROJECTION_URI,
    BIRTH_PRIOR_POLICY,
    CALIBRATION_VERSION,
    DEFAULT_CALIBRATION_POLICY,
    DEFAULT_RATING,
    DIFFICULTY_PROJECTION_URI,
    ELO_SCALE,
    K_ITEM,
    K_LEARNER,
    PRIOR_CLAMP,
    BirthDifficultyVerdict,
    CalibrationVerdict,
    EloUpdate,
    birth_difficulty_prior,
    dynamic_k,
    estimate_birth_difficulty,
    elo_update,
    expected_correct,
    fisher_info_1pl,
    get_ability,
    get_difficulty,
    record_outcome,
    se_from_info,
    seed_item_difficulty,
    update_from_outcome,
)

__all__ = [
    "ABILITY_PROJECTION_URI",
    "BIRTH_PRIOR_POLICY",
    "CALIBRATION_VERSION",
    "DEFAULT_CALIBRATION_POLICY",
    "DEFAULT_RATING",
    "DIFFICULTY_PROJECTION_URI",
    "ELO_SCALE",
    "K_ITEM",
    "K_LEARNER",
    "PRIOR_CLAMP",
    "BirthDifficultyVerdict",
    "CalibrationVerdict",
    "EloUpdate",
    "birth_difficulty_prior",
    "dynamic_k",
    "estimate_birth_difficulty",
    "elo_update",
    "expected_correct",
    "fisher_info_1pl",
    "get_ability",
    "get_difficulty",
    "record_outcome",
    "se_from_info",
    "seed_item_difficulty",
    "update_from_outcome",
]
