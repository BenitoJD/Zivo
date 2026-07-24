"""Next-question selection — compatibility re-exports for the Adaptive Selection Engine.

Canonical implementation: ``app.services.adaptive_selection``
Design: docs/ADAPTIVE_SELECTION_ENGINE.md
Seam: ADR 0004 (swappable policy).

The loop (ask → observe → choose next → repeat) is permanent; *how* the next
question is chosen is a policy that must stay replaceable. Prefer importing
``select_next`` / ``SelectionVerdict`` from ``adaptive_selection`` in new code.
"""

from __future__ import annotations

from app.services.adaptive_selection import (  # noqa: F401
    DEFAULT_SELECTION_POLICY,
    LINK_FOLLOW_UP_AFTER_MISS,
    LINK_HARDER_THAN,
    SELECTION_VERSION,
    TARGET_SUCCESS,
    CandidateSignals,
    LearnerState,
    SelectionScores,
    SelectionVerdict,
    _target_difficulty,
    build_learner_state,
    choose_next_assertion,
    expected_correct,
    fisher_information_1pl,
    normalize_policy,
    select_next,
    target_difficulty,
)

__all__ = [
    "DEFAULT_SELECTION_POLICY",
    "LINK_FOLLOW_UP_AFTER_MISS",
    "LINK_HARDER_THAN",
    "SELECTION_VERSION",
    "TARGET_SUCCESS",
    "CandidateSignals",
    "LearnerState",
    "SelectionScores",
    "SelectionVerdict",
    "_target_difficulty",
    "build_learner_state",
    "choose_next_assertion",
    "expected_correct",
    "fisher_information_1pl",
    "normalize_policy",
    "select_next",
    "target_difficulty",
]
