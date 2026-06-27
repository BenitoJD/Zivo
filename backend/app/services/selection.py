"""Next-question selection — the swappable 'metric' inside the permanent loop.

The loop (ask → observe → choose next → repeat) is permanent; *how* the next
question is chosen is a policy that must stay replaceable (see docs/VISION.md,
"The unchanging core" and docs/adr/0004-swappable-policy-seam.md). Policies:

- ``sequence`` (legacy) — generation order.
- ``concept_reinforce`` — reacts to the last answer's concept.
- ``difficulty_edge`` — targets the productive-struggle band from calibration, with
  per-concept ability and lineage routing (a miss re-approaches via
  ``follow_up_after_miss``; a hit advances via ``harder_than``).

Every policy degrades safely to ``sequence`` when its signal is thin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Aim each question at the productive-struggle band: serve the item this learner is
# expected to get right about TARGET_SUCCESS of the time. On the shared logit scale
# (the same scale the calibrator uses), that is an item a little *below* the learner's
# ability — a desirable difficulty (Bjork): mostly succeeding, genuinely stretched.
# As ability rises, the served items climb with it. 0.75 sits inside the 0.70–0.85
# band that the learning-science evidence (and this work's acceptance gate) calls for.
TARGET_SUCCESS = 0.75

# Lineage link kinds the loop reacts to (suffixes of /vocab/link/*).
LINK_FOLLOW_UP_AFTER_MISS = "follow_up_after_miss"
LINK_HARDER_THAN = "harder_than"


def _target_difficulty(ability: float, target_success: float = TARGET_SUCCESS) -> float:
    """The item difficulty at which `ability` yields `target_success` expected success.

    Inverts the logistic the calibrator assumes: P = 1/(1+e^-(ability-difficulty)),
    so difficulty = ability - logit(P). Lives here (not imported from the calibrator)
    to keep selection a self-contained policy; it only assumes the shared logit scale.
    """
    return ability - math.log(target_success / (1.0 - target_success))


@dataclass(frozen=True)
class LearnerState:
    """What the loop listens to: how the last answer went, which question it was, and
    where this learner sits now — both overall and per concept (when calibrated)."""

    last_concept_key: str | None = None
    last_correct: bool | None = None
    last_assertion_id: str | None = None
    ability: float | None = None
    concept_ability: dict[str, float] | None = None


def build_learner_state(progress: dict) -> LearnerState:
    """Read the learner's latest confirmed answer + calibrated ability from progress."""
    last = progress.get("last_confirmed_answer") or {}
    correct = last.get("correct")
    ability = progress.get("learner_ability")
    concept_ability = progress.get("concept_ability")
    return LearnerState(
        last_concept_key=(last.get("concept_key") or None),
        last_correct=bool(correct) if correct is not None else None,
        last_assertion_id=(str(last["assertion_id"]) if last.get("assertion_id") else None),
        ability=float(ability) if ability is not None else None,
        concept_ability=(
            {str(k): float(v) for k, v in concept_ability.items()}
            if isinstance(concept_ability, dict) and concept_ability
            else None
        ),
    )


def choose_next_assertion(
    policy: str,
    candidates: list[str],
    concept_by_id: dict[str, str | None],
    state: LearnerState,
    difficulty_by_id: dict[str, float] | None = None,
    lineage_by_id: dict[str, str] | None = None,
) -> str | None:
    """Pick the next assertion id from the unanswered candidates (sequence order).

    `candidates` is already in generation/sequence order, so returning
    ``candidates[0]`` reproduces the legacy behaviour exactly — every policy
    degrades to that whenever it has no signal to act on.
    """
    if not candidates:
        return None
    p = (policy or "sequence").lower()
    if p == "concept_reinforce":
        return _concept_reinforce(candidates, concept_by_id, state)
    if p == "difficulty_edge":
        return _difficulty_edge(
            candidates, difficulty_by_id or {}, state, concept_by_id or {}, lineage_by_id or {}
        )
    return candidates[0]


def _ability_for_concept(state: LearnerState, concept: str | None) -> float:
    """The learner's ability for a concept — per-concept when known, else overall.

    Lets a learner who is strong in one concept and weak in another be met in the
    right band on each, rather than averaged into one global level.
    """
    if concept and state.concept_ability is not None:
        per_concept = state.concept_ability.get(concept)
        if per_concept is not None:
            return per_concept
    return state.ability if state.ability is not None else 0.0


def _nearest_to_band(
    candidates: list[str],
    difficulty_by_id: dict[str, float],
    concept_by_id: dict[str, str | None],
    state: LearnerState,
    target_success: float,
) -> str:
    """Return the candidate whose difficulty is closest to the learner's band target.

    The target is computed per candidate from the learner's ability *for that
    candidate's concept*. Cold items (no difficulty yet) are treated as sitting at the
    learner's level so they stay explorable but never beat a known band item. Ties
    resolve to the earlier (sequence-order) candidate.
    """
    best_id = candidates[0]
    best_dist: float | None = None
    for cid in candidates:
        ability = _ability_for_concept(state, concept_by_id.get(cid))
        target = _target_difficulty(ability, target_success)
        difficulty = difficulty_by_id.get(cid)
        if difficulty is None:
            difficulty = ability
        dist = abs(difficulty - target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_id = cid
    return best_id


def _difficulty_edge(
    candidates: list[str],
    difficulty_by_id: dict[str, float],
    state: LearnerState,
    concept_by_id: dict[str, str | None],
    lineage_by_id: dict[str, str],
    *,
    target_success: float = TARGET_SUCCESS,
) -> str:
    """Choose the warm question at this learner's edge, reacting to the last answer.

    1. **Lineage routing** — if the learner just missed, prefer a candidate linked to
       the missed question as a ``follow_up_after_miss`` (a re-approach of the same
       idea); if they just succeeded, prefer a ``harder_than`` successor (advance).
       Among the routed candidates, still pick the one nearest the band.
    2. **Band targeting** — otherwise pick the candidate whose difficulty is closest
       to where the learner succeeds ~``target_success`` of the time, per concept.
    3. **Degrade** — if no candidate is calibrated and no lineage applies, fall back
       to sequence order.

    All O(1) arithmetic over already-warm questions — no LLM, no new generation.
    """
    if state.last_correct is not None and lineage_by_id:
        want = LINK_FOLLOW_UP_AFTER_MISS if state.last_correct is False else LINK_HARDER_THAN
        routed = [cid for cid in candidates if lineage_by_id.get(cid) == want]
        if routed:
            return _nearest_to_band(routed, difficulty_by_id, concept_by_id, state, target_success)

    if not any(difficulty_by_id.get(cid) is not None for cid in candidates):
        return candidates[0]
    return _nearest_to_band(candidates, difficulty_by_id, concept_by_id, state, target_success)


def _concept_reinforce(
    candidates: list[str],
    concept_by_id: dict[str, str | None],
    state: LearnerState,
) -> str:
    """Wrong last answer → reinforce the same concept; right → move to a new one.

    Falls back to sequence order whenever there's no signal or no better match,
    so it can never get stuck or skip a question.
    """
    last = state.last_concept_key
    if not last or state.last_correct is None:
        return candidates[0]

    if state.last_correct is False:
        for cid in candidates:
            if concept_by_id.get(cid) == last:
                return cid
        return candidates[0]

    # Answered correctly — prefer the next question on a *different* concept.
    for cid in candidates:
        if concept_by_id.get(cid) and concept_by_id.get(cid) != last:
            return cid
    return candidates[0]
