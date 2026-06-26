"""Next-question selection — the swappable 'metric' inside the permanent loop.

The loop (ask → observe → choose next → repeat) is permanent; *how* the next
question is chosen is a policy that must stay replaceable (see docs/VISION.md,
"The unchanging core"). Today: sequence (legacy) and concept_reinforce (reacts
to the last answer). Tomorrow: difficulty/edge-targeting backed by IRT — same
seam, no caller changes.
"""

from __future__ import annotations

from dataclasses import dataclass

# Where to aim, in rating units above the learner's current ability. A small
# positive offset is a *desirable* difficulty (Bjork): hard enough to force
# reconstruction (~60-65% expected success), not so hard it becomes noise/guessing.
PRODUCTIVE_STRUGGLE_MARGIN = 0.5


@dataclass(frozen=True)
class LearnerState:
    """The minimal signal the loop listens to: how did the last answer go, and
    where is this learner now (calibrated ability, when available)?"""

    last_concept_key: str | None = None
    last_correct: bool | None = None
    ability: float | None = None


def build_learner_state(progress: dict) -> LearnerState:
    """Read the learner's latest confirmed answer + calibrated ability from progress."""
    last = progress.get("last_confirmed_answer") or {}
    correct = last.get("correct")
    ability = progress.get("learner_ability")
    return LearnerState(
        last_concept_key=(last.get("concept_key") or None),
        last_correct=bool(correct) if correct is not None else None,
        ability=float(ability) if ability is not None else None,
    )


def choose_next_assertion(
    policy: str,
    candidates: list[str],
    concept_by_id: dict[str, str | None],
    state: LearnerState,
    difficulty_by_id: dict[str, float] | None = None,
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
        return _difficulty_edge(candidates, difficulty_by_id or {}, state)
    return candidates[0]


def _difficulty_edge(
    candidates: list[str],
    difficulty_by_id: dict[str, float],
    state: LearnerState,
    *,
    target_margin: float = PRODUCTIVE_STRUGGLE_MARGIN,
) -> str:
    """Choose the warm question whose difficulty sits at this learner's edge.

    Targets ``ability + target_margin`` and returns the candidate closest to it.
    A confident streak lifts ability so the next question climbs; a miss lowers it
    so the next bends back toward where they broke — without any new LLM call, just
    arithmetic over already-warm questions.

    Degrades safely: if not one candidate is calibrated yet (thin data), falls back
    to sequence order. Cold items among calibrated ones are treated as neutral (at
    the learner's ability), so brand-new questions stay explorable rather than
    starved. Ties resolve to the earlier (sequence-order) candidate.
    """
    if not any(difficulty_by_id.get(cid) is not None for cid in candidates):
        return candidates[0]

    ability = state.ability if state.ability is not None else 0.0
    target = ability + target_margin
    best_id = candidates[0]
    best_dist: float | None = None
    for cid in candidates:
        difficulty = difficulty_by_id.get(cid)
        if difficulty is None:
            # Unseen item — assume it sits at the learner's level (neutral) so it
            # competes for selection and gets explored, but never beats a question
            # already known to sit at the edge.
            difficulty = ability
        dist = abs(difficulty - target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_id = cid
    return best_id


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
