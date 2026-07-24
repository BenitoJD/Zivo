"""Misconception / Distractor Engine — incorrect / plausible / diverse.

Design: docs/MISCONCEPTION_DISTRACTOR_ENGINE.md
Version: qb.distractor.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

DISTRACTOR_VERSION = "qb.distractor.v1"

SOFT_CODES = frozenset(
    {
        "implausible_distractors",
        "none_or_all_of_above",
        "longest_option_correct",
        "grammatical_cues",
    }
)


@dataclass(frozen=True)
class DistractorScores:
    incorrectness: float
    plausibility: float
    diversity: float
    overall: float


@dataclass(frozen=True)
class DistractorVerdict:
    ok: bool
    scores: DistractorScores
    flaw_codes: tuple[str, ...]
    policy_version: str = DISTRACTOR_VERSION


def evaluate_distractors(
    options: Sequence[str],
    correct_indices: Sequence[int],
    *,
    heuristic_codes: Sequence[str] | None = None,
) -> DistractorVerdict:
    opts = [str(o).strip() for o in options if str(o).strip()]
    codes = [c for c in (heuristic_codes or []) if c in SOFT_CODES or c]
    soft = tuple(c for c in codes if c in SOFT_CODES)

    # Incorrectness: unique non-empty options and at least one correct index in range.
    correct = [i for i in correct_indices if 0 <= int(i) < len(opts)]
    incorrectness = 1.0 if correct and len(set(opts)) == len(opts) else 0.0

    # Plausibility proxy: length homogeneity (give-away longest correct → lower).
    plausibility = 1.0
    if len(opts) >= 3 and correct:
        lengths = [len(o) for o in opts]
        mean = sum(lengths) / len(lengths) or 1.0
        spread = (max(lengths) - min(lengths)) / mean
        ci = int(correct[0])
        if lengths[ci] == max(lengths) and spread > 0.45:
            plausibility = 0.4
            if "longest_option_correct" not in soft:
                soft = soft + ("longest_option_correct",)
        else:
            plausibility = max(0.3, 1.0 - spread)

    # Diversity: unique token sets among distractors.
    distractors = [o for i, o in enumerate(opts) if i not in set(correct)]
    if len(distractors) <= 1:
        diversity = 1.0
    else:
        sets = [frozenset(o.lower().split()) for o in distractors]
        pairs = 0
        overlap = 0.0
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                pairs += 1
                u = len(sets[i] | sets[j]) or 1
                overlap += len(sets[i] & sets[j]) / u
        diversity = max(0.0, 1.0 - (overlap / pairs if pairs else 0.0))

    if "implausible_distractors" in soft:
        plausibility = min(plausibility, 0.35)
    if "none_or_all_of_above" in soft:
        plausibility = min(plausibility, 0.3)

    overall = (incorrectness + plausibility + diversity) / 3.0
    ok = incorrectness >= 1.0 and overall >= 0.45 and "none_or_all_of_above" not in soft
    return DistractorVerdict(
        ok=ok,
        scores=DistractorScores(incorrectness, plausibility, diversity, overall),
        flaw_codes=soft,
    )
