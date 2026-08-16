"""Real-data gate evaluation — validate on captured outcomes, not planted ones.

The planted-population simulator proved the math is self-consistent; these functions
prove the tutor works on REAL data. They read actual answer events from
``intel.measurement`` and the birth-time difficulty prior from ``intel.projection`` and
compute the acceptance-gate metrics:

  Gate 1 — does the birth prior predict real *first-answer* correctness? (AUC, ECE)

Until a rollout has filled the moat, the reader returns ``{"status": "insufficient_data"}``;
the pure metric functions below are unit-tested, so the gate evaluates with no further
code the moment real sessions exist. Outcomes are the authority — this only scores how
well the prior anticipated them.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import pick

# Need enough real first-answers before a gate verdict is meaningful.
MIN_FIRST_ANSWERS = 100


def _average_ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def auc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """ROC AUC via the Mann–Whitney U statistic. Higher score → predicts label True.

    Returns NaN when one class is absent (AUC undefined). Ties handled by average ranks.
    """
    pos = sum(1 for label in filter(lambda lab: lab, labels))
    neg = len(labels) - pos
    def _auc() -> float:
        ranks = _average_ranks(scores)
        sum_pos = sum(r * float(label) for r, label in zip(ranks, labels))
        u = sum_pos - pos * (pos + 1) / 2.0
        return u / (pos * neg)

    return pick(pos == 0 or neg == 0, lambda: float("nan"), _auc)


def expected_calibration_error(
    probs: Sequence[float], labels: Sequence[bool], *, bins: int = 10
) -> float:
    """Mean gap between predicted probability and observed accuracy, binned by prob."""
    n = len(probs)

    def _ece() -> float:
        bucket_conf = [0.0] * bins
        bucket_hits = [0.0] * bins
        bucket_n = [0] * bins
        for p, label in zip(probs, labels):
            b = min(int(p * bins), bins - 1)
            bucket_conf[b] += p
            bucket_hits[b] += float(label)
            bucket_n[b] += 1
        ece = 0.0
        for b in range(bins):
            def _add(b: int = b) -> None:
                nonlocal ece
                conf = bucket_conf[b] / bucket_n[b]
                acc = bucket_hits[b] / bucket_n[b]
                ece += (bucket_n[b] / n) * abs(conf - acc)

            pick(bool(bucket_n[b]), _add, lambda: None)
        return ece

    return pick(n == 0, lambda: float("nan"), _ece)


def evaluate_prior_gate(db: Session, *, min_n: int = MIN_FIRST_ANSWERS) -> dict:
    """Gate 1 on real data: does birth difficulty predict first-answer correctness?

    For each (learner, item) FIRST answer, scores ``-difficulty`` (a harder item should
    predict an incorrect answer) against the real outcome and reports AUC. A coin-flip
    prior scores ~0.5; the gate wants ≥ 0.70.
    """
    from app.repositories.intel import concept_id
    from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI
    from app.services.calibration import DIFFICULTY_PROJECTION_URI

    rows = db.execute(
        text(
            """
            WITH first_answer AS (
              SELECT DISTINCT ON (subject_entity_id, source_assertion_id)
                     source_assertion_id, value_numeric AS correct
              FROM intel.measurement
              WHERE metric_concept_id = :metric_id
                AND subject_entity_id IS NOT NULL
                AND source_assertion_id IS NOT NULL
              ORDER BY subject_entity_id, source_assertion_id, observed_at ASC
            )
            SELECT fa.correct AS correct,
                   (prior.value->>'rating')::float AS difficulty
            FROM first_answer fa
            JOIN LATERAL (
              SELECT value FROM intel.projection
              WHERE type_concept_id = :difficulty_type
                AND subject_assertion_id = fa.source_assertion_id
              ORDER BY as_of ASC
              LIMIT 1
            ) prior ON true
            """
        ),
        {
            "metric_id": concept_id(db, ANSWER_CORRECT_METRIC_URI),
            "difficulty_type": concept_id(db, DIFFICULTY_PROJECTION_URI),
        },
    ).all()

    def _ok() -> dict:
        scores = [-float(r.difficulty) for r in rows]
        labels = [bool(int(r.correct)) for r in rows]
        a = auc(scores, labels)
        return {"status": "ok", "n": len(rows), "auc": a, "pass": bool(a >= 0.70)}

    return pick(
        len(rows) < min_n,
        lambda: {"status": "insufficient_data", "n": len(rows), "need": min_n},
        _ok,
    )
