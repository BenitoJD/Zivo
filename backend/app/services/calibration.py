"""Online calibration — learns per-item difficulty and per-learner ability from
the real outcomes of real answers.

This is the swappable *metric* inside the permanent loop (see docs/VISION.md and
``app/services/selection.py``): online Elo today, IRT tomorrow — same socket, no
caller changes. It never calls an LLM; every update is O(1) arithmetic on the
answer write-path. And it reads only what a learner actually did, never a model's
guess at difficulty (2025 research: LLMs write items well but cannot reliably judge
difficulty or discrimination — only learners can).

Ratings live on one shared logit scale in ``intel.projection``:
  • per-learner ability   → ``subject_entity_id``,    type ``student.ability``
  • per-item   difficulty → ``subject_assertion_id``, type ``item.difficulty``

A learner whose ability equals an item's difficulty has a 50% expected chance of a
correct answer; the productive-struggle band (see selection.py) sits a little above
that — challenging enough to force reconstruction, not so hard it's noise.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.intel import concept_id

# Vocabulary URIs (seeded by scripts/seed_question_vocab.py).
ABILITY_PROJECTION_URI = "/vocab/projection/student.ability"
DIFFICULTY_PROJECTION_URI = "/vocab/projection/item.difficulty"

# Cold-start rating: ability and difficulty share this origin, so a learner's very
# first answer to an unseen item is a coin-flip until evidence moves them apart.
DEFAULT_RATING = 0.0
# Logistic scale for the expected-score curve. At scale 1.0 a one-unit rating gap
# (≈1 logit) is ~73% expected success.
ELO_SCALE = 1.0
# Step sizes. Learners move faster than items on purpose: a single learner's state
# is volatile and thinly observed, whereas an item's difficulty is a population
# estimate that should settle slowly and never lurch on one strong/weak answer.
K_LEARNER = 0.20
K_ITEM = 0.10


def expected_correct(
    ability: float, difficulty: float, *, scale: float = ELO_SCALE
) -> float:
    """Probability this learner answers an item of this difficulty correctly."""
    return 1.0 / (1.0 + math.exp(-(ability - difficulty) / scale))


@dataclass(frozen=True)
class EloUpdate:
    """Result of one calibration step: the learner's and item's new ratings."""

    ability: float
    difficulty: float
    expected: float


def elo_update(
    ability: float,
    difficulty: float,
    correct: bool,
    *,
    k_learner: float = K_LEARNER,
    k_item: float = K_ITEM,
    scale: float = ELO_SCALE,
) -> EloUpdate:
    """One symmetric Elo step from a single answer outcome.

    The learner gains rating for beating a hard item and loses it for missing an
    easy one; the item moves the opposite way (a miss from a strong learner makes
    it look harder). Pure function — no I/O — so it is trivially unit-tested and
    can be reused by an offline recompute if the metric is ever swapped.
    """
    p = expected_correct(ability, difficulty, scale=scale)
    outcome = 1.0 if correct else 0.0
    new_ability = ability + k_learner * (outcome - p)
    new_difficulty = difficulty + k_item * (p - outcome)
    return EloUpdate(ability=new_ability, difficulty=new_difficulty, expected=p)


def _latest_rating(
    db: Session,
    type_uri: str,
    *,
    entity_id: uuid.UUID | None = None,
    assertion_id: uuid.UUID | None = None,
) -> tuple[float, int]:
    """Most recent (rating, observation-count) for a subject, or the cold-start default.

    Reads the newest projection row via the (type, subject, as_of DESC) index, so it
    is a single point-read regardless of history depth.
    """
    column = "subject_entity_id" if entity_id is not None else "subject_assertion_id"
    subject = entity_id if entity_id is not None else assertion_id
    value = db.execute(
        text(
            f"""
            SELECT value FROM intel.projection
            WHERE type_concept_id = :t AND {column} = :s
            ORDER BY as_of DESC
            LIMIT 1
            """
        ),
        {"t": concept_id(db, type_uri), "s": subject},
    ).scalar()
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict) and value.get("rating") is not None:
        return float(value["rating"]), int(value.get("n") or 0)
    return DEFAULT_RATING, 0


def _append_rating(
    db: Session,
    type_uri: str,
    *,
    now: datetime,
    rating: float,
    n: int,
    entity_id: uuid.UUID | None = None,
    assertion_id: uuid.UUID | None = None,
) -> None:
    """Append one immutable projection row (the table is an append-only time series)."""
    db.execute(
        text(
            """
            INSERT INTO intel.projection
              (type_concept_id, subject_entity_id, subject_assertion_id,
               as_of, value, built_through)
            VALUES (:t, :e, :a, :now, CAST(:value AS jsonb), :now)
            """
        ),
        {
            "t": concept_id(db, type_uri),
            "e": entity_id,
            "a": assertion_id,
            "now": now,
            "value": json.dumps(
                {"rating": round(rating, 6), "n": n, "metric": "elo", "scale": ELO_SCALE}
            ),
        },
    )


def record_outcome(
    db: Session,
    *,
    subject_entity_id: uuid.UUID,
    assertion_id: uuid.UUID,
    correct: bool,
) -> EloUpdate:
    """Apply one Elo step for a graded answer and append the new ability + difficulty.

    Reads the learner's latest ability and the item's latest difficulty (both default
    to ``DEFAULT_RATING`` when unseen), applies :func:`elo_update`, and writes two new
    immutable projection rows so the full history survives for audit and for a future
    IRT refit. O(1): two indexed point-reads + two inserts, no LLM. The caller commits.
    """
    ability, ability_n = _latest_rating(
        db, ABILITY_PROJECTION_URI, entity_id=subject_entity_id
    )
    difficulty, difficulty_n = _latest_rating(
        db, DIFFICULTY_PROJECTION_URI, assertion_id=assertion_id
    )
    update = elo_update(ability, difficulty, correct)
    now = datetime.now(timezone.utc)
    _append_rating(
        db,
        ABILITY_PROJECTION_URI,
        now=now,
        rating=update.ability,
        n=ability_n + 1,
        entity_id=subject_entity_id,
    )
    _append_rating(
        db,
        DIFFICULTY_PROJECTION_URI,
        now=now,
        rating=update.difficulty,
        n=difficulty_n + 1,
        assertion_id=assertion_id,
    )
    return update
