"""Mastery / Evidence-Stop Engine — when to pause a concept or session.

Design: docs/MASTERY_EVIDENCE_ENGINE.md
Version: qb.mastery.v1
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.engine_runtime import Pred, Rule, choose, first_match, pick

MASTERY_VERSION = "qb.mastery.v1"
DEFAULT_MASTERY_POLICY = "elo_proxy_v1"

MASTERY_P = 0.95
MASTERY_MIN_N = 3
SE_TARGET = 0.40
SE_MIN_N = 5

PATH_STRONG = 0.72
PATH_IN_PROGRESS = 0.35
PATH_LABEL_SAMPLE_WINDOW = 5
SD_PATH_SAMPLE_LIMIT = 40
PROGRESS_TOPIC_DISPLAY_LIMIT = 24
PROGRESS_RECENT_DAYS_DEFAULT = 14
PROGRESS_RECENT_DAYS_MAX = 90

StopReason = Literal["mastery", "se_precision", "min_items", "continue"]
PathState = Literal["not_started", "needs_work", "in_progress", "strong"]

_STOP_RULES = (
    Rule(
        when=(Pred("is_test", "truthy"), Pred("n_lt_se_min", "truthy")),
        action="min_items",
        extras={"stop": False},
    ),
    Rule(
        when=(Pred("is_test", "truthy"), Pred("se_ok", "truthy")),
        action="se_precision",
        extras={"stop": True},
    ),
    Rule(when=(Pred("is_test", "truthy"),), action="continue", extras={"stop": False}),
    Rule(
        when=(Pred("n_lt_mastery_min", "truthy"),),
        action="min_items",
        extras={"stop": False},
    ),
    Rule(when=(Pred("p_ok", "truthy"),), action="mastery", extras={"stop": True}),
    Rule(when=(), action="continue", extras={"stop": False}),
)

_PATH_RULES = (
    Rule(when=(Pred("strong", "truthy"),), action="strong"),
    Rule(when=(Pred("progress", "truthy"),), action="in_progress"),
    Rule(when=(), action="needs_work"),
)


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


def _label_with_hist(
    hist: list[float],
    strong_at: float,
    in_progress_at: float,
) -> PathLabelVerdict:
    window = min(PATH_LABEL_SAMPLE_WINDOW, len(hist))
    mastery = round(sum(hist[:window]) / window, 2)
    hit = first_match(
        _PATH_RULES,
        {"strong": mastery >= strong_at, "progress": mastery >= in_progress_at},
    )
    return PathLabelVerdict(hit.action, mastery)  # type: ignore[arg-type]


def label_path_mastery(
    samples: list[float] | None,
    *,
    strong_at: float = PATH_STRONG,
    in_progress_at: float = PATH_IN_PROGRESS,
) -> PathLabelVerdict:
    """Map recent practice scores into UI path state for hubs."""
    hist = list(samples or [])
    return pick(
        not hist,
        lambda: PathLabelVerdict("not_started", None),
        lambda: _label_with_hist(hist, strong_at, in_progress_at),
    )


def plan_sd_path_sample_limit() -> int:
    """How many recent system-design sessions feed path-mastery samples."""
    return SD_PATH_SAMPLE_LIMIT


def plan_progress_topic_display_limit() -> int:
    """How many weakest-topic rows the progress analytics view may show."""
    return PROGRESS_TOPIC_DISPLAY_LIMIT


def plan_progress_recent_days(requested: int | None = None) -> int:
    """How many recent days the progress journal aggregates."""
    n = pick(requested is None, lambda: PROGRESS_RECENT_DAYS_DEFAULT, lambda: int(requested))
    return max(1, min(n, PROGRESS_RECENT_DAYS_MAX))


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
    avg = pick(
        bool(vals),
        lambda: (sum(vals) / len(vals) - 1) / 3,
        lambda: SD_NEUTRAL_MASTERY,
    )
    sample = avg - choose(is_weak, SD_WEAK_PENALTY, 0.0)
    return max(0.0, min(1.0, sample))


def plan_path_focus(items: list[tuple[str, PathState]]) -> str | None:
    """Next practice-hub concept: first non-strong, else the first item."""
    focused = tuple(filter(lambda item: item[1] in PATH_FOCUS_STATES, items))
    return pick(
        bool(focused),
        lambda: focused[0][0],
        lambda: pick(bool(items), lambda: items[0][0], lambda: None),
    )


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
    info = float(running_info or 0.0)
    info = pick(info <= 0 and n > 0, lambda: 0.2 * n, lambda: info)
    se = pick(
        se_theta is None,
        lambda: 1.0 / math.sqrt(max(info, 1e-6)),
        lambda: float(se_theta),
    )
    hit = first_match(
        _STOP_RULES,
        {
            "is_test": (mode or "learn").lower() == "test",
            "n_lt_se_min": n < SE_MIN_N,
            "n_lt_mastery_min": n < MASTERY_MIN_N,
            "se_ok": se <= SE_TARGET,
            "p_ok": p >= MASTERY_P,
        },
    )
    return StopVerdict(bool(hit.extras["stop"]), hit.action, p, se, n)  # type: ignore[arg-type]
