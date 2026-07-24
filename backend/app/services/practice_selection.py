"""Practice Selection Engine — next problem for coding / system-design hubs.

Design: docs/ADAPTIVE_SELECTION_ENGINE.md (practice sibling; same ADR 0004 seam)
Version: qb.practice_sel.v1

Owns overlap×difficulty ranking. Callers must not cram if-else score ladders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

PRACTICE_SEL_VERSION = "qb.practice_sel.v1"
DEFAULT_POLICY = "overlap_v1"

_DIFF_SCORE = {"easy": 1, "medium": 2, "hard": 3}


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
) -> int:
    focus_set = {str(x).strip().lower() for x in focus if str(x).strip()}
    keys = {str(x).strip().lower() for x in candidate.concept_keys if str(x).strip()}
    overlap = len(focus_set & keys) if focus_set else 0
    diff = _DIFF_SCORE.get((candidate.difficulty or "").strip().lower(), 0)
    return overlap * 10 + diff


def pick_next(
    candidates: Sequence[PracticeCandidate],
    focus: Sequence[str],
    *,
    exclude_id: str | None = None,
    policy: str | None = None,
) -> PracticePick:
    """Pick highest overlap×difficulty; ties keep first-seen order. Empty → None."""
    pol = (policy or DEFAULT_POLICY).strip().lower() or DEFAULT_POLICY
    best_id: str | None = None
    best_score = -1
    for c in candidates:
        if exclude_id and str(c.id) == str(exclude_id):
            continue
        s = score_candidate(c, focus)
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
    return pick_next(cands, focus, exclude_id=exclude_id)
