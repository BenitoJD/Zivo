"""Practice Selection Engine — next problem for coding / system-design hubs.

Design: docs/ADAPTIVE_SELECTION_ENGINE.md (practice sibling; same ADR 0004 seam)
Version: qb.practice_sel.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from app.engine_runtime import Pred, Rule, apply, first_match, pick

PRACTICE_SEL_VERSION = "qb.practice_sel.v1"
DEFAULT_POLICY = "overlap_v1"

_DIFF_SCORE = {"easy": 1, "medium": 2, "hard": 3}
ATTEMPT_BONUS = 5
ATTEMPT_PENALTY = 3
CODING_NEXT_POOL_LIMIT = 80


def plan_coding_next_pool_limit() -> int:
    """How many published coding problems to rank before picking the next one."""
    return CODING_NEXT_POOL_LIMIT


@dataclass(frozen=True)
class PracticeCandidate:
    id: str
    concept_keys: tuple[str, ...] = ()
    difficulty: str | None = None


@dataclass(frozen=True)
class PracticePick:
    id: str | None
    score: int
    policy: str = DEFAULT_POLICY
    policy_version: str = PRACTICE_SEL_VERSION


_ATTEMPT_BIAS_RULES = (
    Rule(when=(Pred("unknown", "truthy"),), action="none"),
    Rule(when=(Pred("attempted", "truthy"),), action="penalty"),
    Rule(when=(), action="bonus"),
)

_PICK_NEXT_RULES = (
    Rule(when=(Pred("empty", "truthy"),), action="none"),
    Rule(when=(), action="best"),
)


def score_candidate(
    candidate: PracticeCandidate,
    focus: Sequence[str],
    *,
    attempted: bool | None = None,
) -> int:
    """Overlap×difficulty; optional attempt bias (+bonus unattempted / −penalty done)."""
    focus_set = {str(x).strip().lower() for x in filter(lambda x: str(x).strip(), focus)}
    keys = {str(x).strip().lower() for x in filter(lambda x: str(x).strip(), candidate.concept_keys)}
    overlap = len(focus_set & keys)
    diff = _DIFF_SCORE.get((candidate.difficulty or "").strip().lower(), 0)
    base = overlap * 10 + diff
    return apply(
        first_match(
            _ATTEMPT_BIAS_RULES,
            {"unknown": attempted is None, "attempted": bool(attempted)},
        ).action,
        {
            "none": lambda: base,
            "penalty": lambda: base - ATTEMPT_PENALTY,
            "bonus": lambda: base + ATTEMPT_BONUS,
        },
    )


def pick_next(
    candidates: Sequence[PracticeCandidate],
    focus: Sequence[str],
    *,
    exclude_id: str | None = None,
    attempted_ids: Sequence[str] | None = None,
    policy: str | None = None,
) -> PracticePick:
    """Pick highest overlap×difficulty (+ attempt bias when attempted_ids given)."""
    pol = (policy or DEFAULT_POLICY).strip().lower() or DEFAULT_POLICY
    attempted_set = pick(
        attempted_ids is None,
        lambda: None,
        lambda: {str(x) for x in attempted_ids},
    )
    ranked = tuple(
        filter(
            lambda c: not (exclude_id and str(c.id) == str(exclude_id)),
            candidates,
        )
    )
    scored = tuple(
        (
            c,
            score_candidate(
                c,
                focus,
                attempted=pick(
                    attempted_set is None,
                    lambda: None,
                    lambda: str(c.id) in attempted_set,
                ),
            ),
        )
        for c in ranked
    )
    best = max(scored, key=lambda pair: pair[1], default=None)
    return apply(
        first_match(_PICK_NEXT_RULES, {"empty": best is None}).action,
        {
            "none": lambda: PracticePick(id=None, score=0, policy=pol),
            "best": lambda: PracticePick(id=best[0].id, score=max(0, best[1]), policy=pol),
        },
    )


def pick_from_rows(
    rows: Sequence[Mapping[str, object]],
    focus: Sequence[str],
    *,
    id_key: str = "id",
    concepts_key: str = "concept_keys",
    difficulty_key: str = "difficulty",
    exclude_id: str | None = None,
    attempted_ids: Sequence[str] | None = None,
) -> PracticePick:
    def keys_of(raw_keys: object) -> tuple[str, ...]:
        return pick(
            isinstance(raw_keys, str),
            lambda: (str(raw_keys),),
            lambda: tuple(str(x) for x in (raw_keys or ())),
        )

    cands = [
        PracticeCandidate(
            id=str(r[id_key]),
            concept_keys=keys_of(r.get(concepts_key) or ()),
            difficulty=str(r.get(difficulty_key) or "") or None,
        )
        for r in rows
    ]
    return pick_next(
        cands,
        focus,
        exclude_id=exclude_id,
        attempted_ids=attempted_ids,
    )
