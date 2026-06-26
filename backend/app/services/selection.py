"""Next-question selection — the swappable 'metric' inside the permanent loop.

The loop (ask → observe → choose next → repeat) is permanent; *how* the next
question is chosen is a policy that must stay replaceable (see docs/VISION.md,
"The unchanging core"). Today: sequence (legacy) and concept_reinforce (reacts
to the last answer). Tomorrow: difficulty/edge-targeting backed by IRT — same
seam, no caller changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LearnerState:
    """The minimal signal the loop listens to: how did the last answer go?"""

    last_concept_key: str | None = None
    last_correct: bool | None = None


def build_learner_state(progress: dict) -> LearnerState:
    """Read the learner's latest confirmed answer from learn-state progress."""
    last = progress.get("last_confirmed_answer") or {}
    correct = last.get("correct")
    return LearnerState(
        last_concept_key=(last.get("concept_key") or None),
        last_correct=bool(correct) if correct is not None else None,
    )


def choose_next_assertion(
    policy: str,
    candidates: list[str],
    concept_by_id: dict[str, str | None],
    state: LearnerState,
) -> str | None:
    """Pick the next assertion id from the unanswered candidates (sequence order).

    `candidates` is already in generation/sequence order, so returning
    ``candidates[0]`` reproduces the legacy behaviour exactly.
    """
    if not candidates:
        return None
    if (policy or "sequence").lower() == "concept_reinforce":
        return _concept_reinforce(candidates, concept_by_id, state)
    return candidates[0]


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
