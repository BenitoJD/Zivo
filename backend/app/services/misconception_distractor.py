"""Misconception / Distractor Engine — incorrect / plausible / diverse.

Design: docs/MISCONCEPTION_DISTRACTOR_ENGINE.md
Version: qb.distractor.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick

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


def _plausibility_pair(
    opts: list[str],
    correct: list[int],
    soft: tuple[str, ...],
) -> tuple[float, tuple[str, ...]]:
    lengths = [len(o) for o in opts]
    mean = sum(lengths) / len(lengths) or 1.0
    spread = (max(lengths) - min(lengths)) / mean
    ci = int(correct[0])
    long_giveaway = lengths[ci] == max(lengths) and spread > 0.45
    plausibility = choose(long_giveaway, 0.4, max(0.3, 1.0 - spread))
    extra = pick(
        long_giveaway and "longest_option_correct" not in soft,
        lambda: ("longest_option_correct",),
        lambda: (),
    )
    return plausibility, soft + extra


def _diversity(distractors: list[str]) -> float:
    sets = [frozenset(o.lower().split()) for o in distractors]
    pairs = 0
    overlap = 0.0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            pairs += 1
            union = len(sets[i] | sets[j]) or 1
            overlap += len(sets[i] & sets[j]) / union
    return max(0.0, 1.0 - pick(bool(pairs), lambda: overlap / pairs, lambda: 0.0))


_PLAUSIBILITY_SHAPE_RULES = (
    Rule(when=(Pred("enough", "truthy"),), action="pair"),
    Rule(when=(), action="full"),
)

_DIVERSITY_COUNT_RULES = (
    Rule(when=(Pred("few", "truthy"),), action="full"),
    Rule(when=(), action="compute"),
)


def evaluate_distractors(
    options: Sequence[str],
    correct_indices: Sequence[int],
    *,
    heuristic_codes: Sequence[str] | None = None,
) -> DistractorVerdict:
    opts = [str(o).strip() for o in filter(lambda o: str(o).strip(), options)]
    codes = list(filter(None, heuristic_codes or []))
    soft = tuple(filter(SOFT_CODES.__contains__, codes))
    correct = list(filter(lambda i: 0 <= int(i) < len(opts), correct_indices))
    incorrectness = choose(bool(correct) and len(set(opts)) == len(opts), 1.0, 0.0)
    plausibility, soft = apply(
        first_match(
            _PLAUSIBILITY_SHAPE_RULES,
            {"enough": len(opts) >= 3 and bool(correct)},
        ).action,
        {
            "pair": lambda: _plausibility_pair(opts, correct, soft),
            "full": lambda: (1.0, soft),
        },
    )
    correct_set = set(correct)
    distractors = [
        o for _i, o in filter(lambda pair: pair[0] not in correct_set, enumerate(opts))
    ]
    diversity = apply(
        first_match(_DIVERSITY_COUNT_RULES, {"few": len(distractors) <= 1}).action,
        {
            "full": lambda: 1.0,
            "compute": lambda: _diversity(distractors),
        },
    )
    plausibility = min(plausibility, choose("implausible_distractors" in soft, 0.35, 1.0))
    plausibility = min(plausibility, choose("none_or_all_of_above" in soft, 0.3, 1.0))
    overall = (incorrectness + plausibility + diversity) / 3.0
    ok = incorrectness >= 1.0 and overall >= 0.45 and "none_or_all_of_above" not in soft
    return DistractorVerdict(
        ok=ok,
        scores=DistractorScores(incorrectness, plausibility, diversity, overall),
        flaw_codes=soft,
    )
