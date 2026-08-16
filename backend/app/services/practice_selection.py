"""Practice Selection Engine — next problem for coding / system-design hubs.

Design: docs/ADAPTIVE_SELECTION_ENGINE.md (practice sibling; same ADR 0004 seam)
Version: qb.practice_sel.v1

Owns overlap×difficulty ranking and attempt bias. Callers must not cram if-else
score ladders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

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


def score_candidate(
    candidate: PracticeCandidate,
    focus: Sequence[str],
    *,
    attempted: bool | None = None,
) -> int:
    """Overlap×difficulty; optional attempt bias (+bonus unattempted / −penalty done)."""
    focus_set = {str(x).strip().lower() for x in focus if str(x).strip()}
    keys = {str(x).strip().lower() for x in candidate.concept_keys if str(x).strip()}
    overlap = len(focus_set & keys) if focus_set else 0
    diff = _DIFF_SCORE.get((candidate.difficulty or "").strip().lower(), 0)
    base = overlap * 10 + diff
    if attempted is None:
        return base
    return base + (ATTEMPT_BONUS if not attempted else -ATTEMPT_PENALTY)


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
    attempted_set = (
        {str(x) for x in attempted_ids} if attempted_ids is not None else None
    )
    best_id: str | None = None
    best_score = -1
    for c in candidates:
        if exclude_id and str(c.id) == str(exclude_id):
            continue
        attempted = None if attempted_set is None else str(c.id) in attempted_set
        s = score_candidate(c, focus, attempted=attempted)
        if s > best_score:
            best_score = s
            best_id = c.id
    return PracticePick(
        id=best_id,
        score=max(0, best_score),
        policy=pol,
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
    cands: list[PracticeCandidate] = []
    for r in rows:
        raw_keys = r.get(concepts_key) or ()
        if isinstance(raw_keys, str):
            keys: tuple[str, ...] = (raw_keys,)
        else:
            keys = tuple(str(x) for x in raw_keys)  # type: ignore[arg-type]
        cands.append(
            PracticeCandidate(
                id=str(r[id_key]),
                concept_keys=keys,
                difficulty=str(r.get(difficulty_key) or "") or None,
            )
        )
    return pick_next(
        cands,
        focus,
        exclude_id=exclude_id,
        attempted_ids=attempted_ids,
    )
