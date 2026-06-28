"""Reproducible replay harness for the adaptive tutor.

Replays a stream of answer outcomes — synthetic (planted truth) or real
``intel.measurement`` rows — through the **real** calibrator
(:func:`app.services.calibration.elo_update`) and the **real** logistic model
(:func:`app.services.calibration.expected_correct`). Nothing is reimplemented or
mocked, so the numbers it emits are about the shipping tutor, not a parallel copy.

It exists to make "the tutor adapts" falsifiable:
  • Gate 1 — parameter recovery: does online Elo recover planted item difficulty?
  • Gate 2 — adaptation efficacy: does ``difficulty_edge`` beat ``sequence`` on
    mastery gained per question while holding the productive-struggle band?
  • Gate 3 — robustness: does the layer survive adversarial event streams and
    degenerate pools without crashing, and abstain when there is nothing to ask?
  • Gate 4 — seamless: does selection stay well under the latency budget with many
    concurrent simulated learners, and is per-event calibration cost flat (O(1))?

All randomness is seeded, so every number here is reproducible.
"""

from __future__ import annotations

import concurrent.futures
import math
import random
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.services.calibration import (
    DEFAULT_RATING,
    ELO_SCALE,
    K_ITEM,
    K_LEARNER,
    elo_update,
    expected_correct,
)
from app.services.selection import LearnerState, choose_next_assertion


@dataclass(frozen=True)
class AnswerEvent:
    """One observed outcome: ``learner`` answered ``item`` (correctly or not)."""

    learner: str
    item: str
    correct: bool


@dataclass
class ReplayResult:
    """Estimates after replaying a stream, plus the model's pre-outcome predictions."""

    ability: dict[str, float]
    difficulty: dict[str, float]
    predicted: list[float]  # model P(correct) computed *before* seeing each outcome
    outcomes: list[bool]


def replay(
    events: Iterable[AnswerEvent],
    *,
    k_learner: float = K_LEARNER,
    k_item: float = K_ITEM,
    scale: float = ELO_SCALE,
) -> ReplayResult:
    """Fold a stream of answers through the real online-Elo update.

    Cold subjects start at ``DEFAULT_RATING``; each event reads the current
    ability/difficulty, applies :func:`elo_update`, and writes them back — exactly
    what ``record_outcome`` does per answer, minus the DB. ``predicted`` is the
    model's P(correct) *before* the outcome (so it measures genuine prediction, not
    hindsight).
    """
    ability: dict[str, float] = {}
    difficulty: dict[str, float] = {}
    predicted: list[float] = []
    outcomes: list[bool] = []
    for event in events:
        a = ability.get(event.learner, DEFAULT_RATING)
        d = difficulty.get(event.item, DEFAULT_RATING)
        update = elo_update(a, d, event.correct, k_learner=k_learner, k_item=k_item, scale=scale)
        ability[event.learner] = update.ability
        difficulty[event.item] = update.difficulty
        predicted.append(update.expected)
        outcomes.append(event.correct)
    return ReplayResult(ability=ability, difficulty=difficulty, predicted=predicted, outcomes=outcomes)


# ---------------------------------------------------------------------------
# Synthetic population — planted truth for parameter recovery
# ---------------------------------------------------------------------------


def simulate_population(
    n_learners: int, n_items: int, *, seed: int = 0
) -> tuple[dict[str, float], dict[str, float]]:
    """Plant true learner abilities and item difficulties ~ N(0, 1)."""
    rng = random.Random(seed)
    abilities = {f"L{i}": rng.gauss(0.0, 1.0) for i in range(n_learners)}
    difficulties = {f"I{j}": rng.gauss(0.0, 1.0) for j in range(n_items)}
    return abilities, difficulties


def generate_events(
    abilities: dict[str, float],
    difficulties: dict[str, float],
    *,
    seed: int = 0,
    scale: float = ELO_SCALE,
) -> list[AnswerEvent]:
    """Every learner answers every item once; correctness ~ Bernoulli(P).

    P is the **same** logistic the calibrator assumes (1PL/Rasch via
    :func:`expected_correct`), so recovery tests the estimator, not a model
    mismatch. Pairs are globally shuffled so each item is observed across the full
    spread of abilities (and vice-versa).
    """
    rng = random.Random(seed)
    pairs = [(learner, item) for learner in abilities for item in difficulties]
    rng.shuffle(pairs)
    events: list[AnswerEvent] = []
    for learner, item in pairs:
        p = expected_correct(abilities[learner], difficulties[item], scale=scale)
        events.append(AnswerEvent(learner=learner, item=item, correct=(rng.random() < p)))
    return events


# ---------------------------------------------------------------------------
# Metrics (no scipy dependency — Spearman = Pearson on average ranks)
# ---------------------------------------------------------------------------


def _average_ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n == 0:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Spearman rank correlation in [-1, 1]."""
    return _pearson(_average_ranks(a), _average_ranks(b))


def recover_parameters(
    *,
    n_learners: int = 300,
    n_items: int = 60,
    seed: int = 0,
    k_item: float = K_ITEM,
    k_learner: float = K_LEARNER,
) -> dict[str, float]:
    """Gate 1: plant truth, simulate answers, replay, and score recovery.

    Returns the rank correlation between estimated and true item difficulty (the
    gate metric) and learner ability, plus the event count for the record.
    """
    abilities, difficulties = simulate_population(n_learners, n_items, seed=seed)
    events = generate_events(abilities, difficulties, seed=seed + 1)
    result = replay(events, k_item=k_item, k_learner=k_learner)

    items = list(difficulties)
    learners = list(abilities)
    return {
        "difficulty_spearman": spearman(
            [result.difficulty[i] for i in items], [difficulties[i] for i in items]
        ),
        "ability_spearman": spearman(
            [result.ability[learner] for learner in learners],
            [abilities[learner] for learner in learners],
        ),
        "n_events": float(len(events)),
        "n_learners": float(n_learners),
        "n_items": float(n_items),
    }


# ---------------------------------------------------------------------------
# Gate 2 — adaptation efficacy: does difficulty_edge beat sequence?
# ---------------------------------------------------------------------------

# Desirable-difficulty learning curve: a question teaches most when the learner is
# likely-but-not-certain to get it right (~PEAK success). Too easy (p→1) is boredom,
# too hard (p→0) is noise — both teach little. This is the testing-effect hypothesis
# the tutor is built on, stated *explicitly* so the result is falsifiable rather than
# smuggled in: the selector wins only if it actually keeps the learner in this band.
_GAIN_PEAK = 0.78
_GAIN_WIDTH = 0.18


def learning_gain(success_prob: float) -> float:
    """Relative learning from one question, peaking at ~`_GAIN_PEAK` success prob."""
    return math.exp(-(((success_prob - _GAIN_PEAK) / _GAIN_WIDTH) ** 2))


def simulate_learner(
    policy: str,
    item_difficulties: dict[str, float],
    *,
    n_questions: int,
    seed: int = 0,
    learning_rate: float = 0.06,
    k_learner: float = K_LEARNER,
) -> dict[str, float]:
    """One learner answering `n_questions` from a fixed pool under `policy`.

    The learner's *true* ability grows by ``learning_rate * learning_gain(p)`` each
    question (they learn most in the productive band). The tutor only knows the
    Elo-tracked estimate from outcomes — and ``difficulty_edge`` (the real selector)
    chooses from that estimate, never the hidden truth. ``sequence`` ignores it.
    """
    rng = random.Random(seed)
    theta_true = 0.0
    theta_est = DEFAULT_RATING
    remaining = list(item_difficulties.keys())  # insertion order == sequence order
    served_success: list[float] = []
    for _ in range(min(n_questions, len(remaining))):
        if policy == "difficulty_edge":
            diff_map = {i: item_difficulties[i] for i in remaining}
            item = choose_next_assertion(
                "difficulty_edge", remaining, {}, LearnerState(ability=theta_est), diff_map
            )
        else:
            item = remaining[0]
        if item is None:  # real guard, not `assert` (which -O strips)
            break
        remaining.remove(item)

        difficulty = item_difficulties[item]
        p = expected_correct(theta_true, difficulty)
        correct = rng.random() < p
        served_success.append(p)
        theta_true += learning_rate * learning_gain(p)
        theta_est = elo_update(theta_est, difficulty, correct, k_learner=k_learner).ability

    return {
        "mastery_gain": theta_true,
        "mean_success": sum(served_success) / len(served_success) if served_success else 0.0,
    }


def adaptation_efficacy(
    *,
    n_items: int = 80,
    n_questions: int = 40,
    n_learners: int = 200,
    seed: int = 0,
    learning_rate: float = 0.06,
) -> dict[str, float]:
    """Gate 2: compare difficulty_edge vs sequence on the SAME pools/learners.

    Each simulated learner faces one randomly-drawn item pool; both policies run on
    that identical pool (fair fight — the only difference is which question is served
    next). Returns mean mastery gain per policy and the mean served success rate for
    difficulty_edge (which must land in the productive-struggle band).
    """
    pool_rng = random.Random(seed)
    edge_mastery: list[float] = []
    seq_mastery: list[float] = []
    edge_success: list[float] = []
    for learner_idx in range(n_learners):
        pool = {f"I{j}": pool_rng.gauss(0.0, 1.2) for j in range(n_items)}
        edge = simulate_learner(
            "difficulty_edge", pool, n_questions=n_questions,
            seed=seed * 100_003 + learner_idx, learning_rate=learning_rate,
        )
        seq = simulate_learner(
            "sequence", pool, n_questions=n_questions,
            seed=seed * 100_003 + learner_idx, learning_rate=learning_rate,
        )
        edge_mastery.append(edge["mastery_gain"])
        seq_mastery.append(seq["mastery_gain"])
        edge_success.append(edge["mean_success"])

    n = float(n_learners)
    edge_mean = sum(edge_mastery) / n
    seq_mean = sum(seq_mastery) / n
    return {
        "edge_mastery_per_q": edge_mean / n_questions,
        "sequence_mastery_per_q": seq_mean / n_questions,
        "mastery_ratio": edge_mean / seq_mean if seq_mean > 0 else float("inf"),
        "edge_mean_success": sum(edge_success) / n,
        "n_learners": n,
    }


# ---------------------------------------------------------------------------
# Gate 4 — seamless under load: selection latency + O(1) calibration
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0, 1]); used for p95 latency."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * q
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] * (hi - k) + ordered[hi] * (k - lo)


def _warm_pool(pool_size: int) -> dict[str, float]:
    """A realistic warm pool of calibrated items spanning roughly [-2, 2] logits."""
    if pool_size == 1:
        return {"I0": 0.0}
    return {f"I{i}": (i / (pool_size - 1)) * 4.0 - 2.0 for i in range(pool_size)}


def selection_latencies(
    *,
    n_workers: int = 16,
    calls_per_worker: int = 500,
    pool_size: int = 12,
    seed: int = 0,
) -> list[float]:
    """Wall-clock seconds per ``choose_next_assertion`` call across concurrent workers.

    Each worker is a simulated learner hammering the real selector over a warm pool;
    running them on a thread pool exercises the answer-path under concurrency. The
    selector is pure arithmetic, so this measures the real read-path cost.
    """
    pool = _warm_pool(pool_size)
    candidates = list(pool)

    def work(worker_id: int) -> list[float]:
        rng = random.Random(seed * 7919 + worker_id)
        out: list[float] = []
        for _ in range(calls_per_worker):
            state = LearnerState(ability=rng.gauss(0.0, 1.2))
            start = time.perf_counter()
            choose_next_assertion("difficulty_edge", candidates, {}, state, pool)
            out.append(time.perf_counter() - start)
        return out

    latencies: list[float] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool_exec:
        for chunk in pool_exec.map(work, range(n_workers)):
            latencies.extend(chunk)
    return latencies


def calibration_per_event_seconds(n_events: int, *, seed: int = 0) -> float:
    """Mean wall-clock seconds per online-Elo update over a stream of `n_events`.

    Online Elo reads/writes two dict entries plus fixed arithmetic, so per-event
    cost must not grow with stream length. Comparing this across sizes shows O(1).
    """
    rng = random.Random(seed)
    abilities: dict[str, float] = {}
    difficulties: dict[str, float] = {}
    learners = [f"L{i}" for i in range(max(1, n_events // 50))]
    items = [f"I{i}" for i in range(max(1, n_events // 50))]
    start = time.perf_counter()
    for _ in range(n_events):
        learner = rng.choice(learners)
        item = rng.choice(items)
        a = abilities.get(learner, DEFAULT_RATING)
        d = difficulties.get(item, DEFAULT_RATING)
        update = elo_update(a, d, rng.random() < expected_correct(a, d))
        abilities[learner] = update.ability
        difficulties[item] = update.difficulty
    return (time.perf_counter() - start) / n_events
