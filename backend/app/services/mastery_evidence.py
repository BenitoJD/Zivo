"""Mastery / Evidence-Stop Engine — when to pause a concept or session.

Design: docs/MASTERY_EVIDENCE_ENGINE.md
Version: qb.mastery.v1
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

MASTERY_VERSION = "qb.mastery.v1"
DEFAULT_MASTERY_POLICY = "elo_proxy_v1"

MASTERY_P = 0.95
MASTERY_MIN_N = 3
SE_TARGET = 0.40
SE_MIN_N = 5

# Practice-hub path labeling (system-design concept map).
PATH_STRONG = 0.72
PATH_IN_PROGRESS = 0.35

StopReason = Literal["mastery", "se_precision", "min_items", "continue"]
PathState = Literal["not_started", "needs_work", "in_progress", "strong"]


@dataclass(frozen=True)
class StopVerdict:
    stop: bool
    reason: StopReason
    p_mastery: float
    se_theta: float
    n: int
    policy_version: str = MASTERY_VERSION


@dataclass(frozen=True)
class PathLabelVerdict:
    state: PathState
    mastery: float | None
    policy_version: str = MASTERY_VERSION


def label_path_mastery(
    samples: list[float] | None,
    *,
    strong_at: float = PATH_STRONG,
    in_progress_at: float = PATH_IN_PROGRESS,
) -> PathLabelVerdict:
    """Map recent practice scores into UI path state for hubs."""
    hist = list(samples or [])
    if not hist:
        return PathLabelVerdict("not_started", None)
    mastery = round(sum(hist[:5]) / min(5, len(hist)), 2)
    if mastery >= strong_at:
        state: PathState = "strong"
    elif mastery >= in_progress_at:
        state = "in_progress"
    else:
        state = "needs_work"
    return PathLabelVerdict(state, mastery)


PATH_FOCUS_STATES: tuple[PathState, ...] = ("not_started", "needs_work", "in_progress")
SD_NEUTRAL_MASTERY = 0.4
SD_WEAK_PENALTY = 0.25


def sample_path_mastery(
    *,
    dimension_scores: list[float] | None,
    is_weak: bool,
) -> float:
    """Map one SD session into a 0-1 mastery sample for a concept."""
    vals = [float(v) for v in (dimension_scores or [])]
    if vals:
        avg = (sum(vals) / len(vals) - 1) / 3
    else:
        avg = SD_NEUTRAL_MASTERY
    sample = avg - (SD_WEAK_PENALTY if is_weak else 0.0)
    return max(0.0, min(1.0, sample))


def plan_path_focus(items: list[tuple[str, PathState]]) -> str | None:
    """Next practice-hub concept: first non-strong, else the first item."""
    for key, state in items:
        if state in PATH_FOCUS_STATES:
            return key
    if items:
        return items[0][0]
    return None


def p_mastery_from_ability(ability: float, *, threshold_difficulty: float = 0.0) -> float:
    """Logistic P(correct) vs a reference difficulty as a cheap mastery proxy."""
    return 1.0 / (1.0 + math.exp(-(ability - threshold_difficulty)))


def evaluate_stop(
    *,
    ability: float,
    n: int,
    se_theta: float | None = None,
    mode: str = "learn",
    running_info: float | None = None,
) -> StopVerdict:
    n = max(0, int(n))
    p = p_mastery_from_ability(ability)
    if se_theta is None:
        info = float(running_info or 0.0)
        if info <= 0 and n > 0:
            info = 0.2 * n
        se_theta = 1.0 / math.sqrt(max(info, 1e-6))
    mode = (mode or "learn").lower()
    if mode == "test":
        if n < SE_MIN_N:
            return StopVerdict(False, "min_items", p, se_theta, n)
        if se_theta <= SE_TARGET:
            return StopVerdict(True, "se_precision", p, se_theta, n)
        return StopVerdict(False, "continue", p, se_theta, n)
    # Learn: mastery threshold with min evidence.
    if n < MASTERY_MIN_N:
        return StopVerdict(False, "min_items", p, se_theta, n)
    if p >= MASTERY_P:
        return StopVerdict(True, "mastery", p, se_theta, n)
    return StopVerdict(False, "continue", p, se_theta, n)
