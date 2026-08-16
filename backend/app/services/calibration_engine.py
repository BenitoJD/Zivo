"""Calibration Engine - online Elo + SE (ADR 0004 seam).

Design: docs/CALIBRATION_ENGINE.md
Version: qb.calibration.v1

Sub-policy ``birth_prior_v1``: cold-start item difficulty from MCQ features
(``estimate_birth_difficulty``). Outcomes remain the authority after first grade.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, first_match
from app.repositories.intel import concept_id

CALIBRATION_VERSION = "qb.calibration.v1"
DEFAULT_CALIBRATION_POLICY = "elo_online_v1"
BIRTH_PRIOR_POLICY = "birth_prior_v1"

ABILITY_PROJECTION_URI = "/vocab/projection/student.ability"
DIFFICULTY_PROJECTION_URI = "/vocab/projection/item.difficulty"

DEFAULT_RATING = 0.0
ELO_SCALE = 1.0
K_LEARNER = 0.20
K_ITEM = 0.10
# Dynamic K: k = k_base * (1 + K_COLD_BOOST / (1 + n))
K_COLD_BOOST = 2.0
INFO_EPS = 1e-6

# Keep the birth prior modest: real outcomes should dominate within a handful of answers.
PRIOR_CLAMP = 2.0

# Higher-order cognition (apply/analyse/explain-why) tends to be harder than recall.
_HARD_VERBS = re.compile(
    r"\b(appl(?:y|ies|ied)|predict|compare|contrast|analy[sz]e|evaluate|infer|"
    r"distinguish|deriv(?:e|es|ed)|justif(?:y|ies)|synthesi[sz]e|interpret|why|how)\b",
    re.IGNORECASE,
)
_EASY_VERBS = re.compile(
    r"\b(define|identif(?:y|ies)|name|list|recall|state|label|who|when|where|"
    r"what\s+is|which\s+of)\b",
    re.IGNORECASE,
)

_SPREAD_RULES = (
    Rule(when=(Pred("mean_ok", "truthy"),), action="spread"),
    Rule(when=(), action="zero"),
)
_HARD_VERB_RULES = (
    Rule(when=(Pred("hit", "truthy"),), action="add", extras={"delta": 0.6}),
    Rule(when=(), action="none", extras={"delta": 0.0}),
)
_EASY_VERB_RULES = (
    Rule(when=(Pred("hit", "truthy"),), action="add", extras={"delta": -0.6}),
    Rule(when=(), action="none", extras={"delta": 0.0}),
)
_OPTION_COUNT_RULES = (
    Rule(when=(Pred("has_options", "truthy"),), action="guess_rate"),
    Rule(when=(), action="none"),
)
_OPTION_SPREAD_RULES = (
    Rule(when=(Pred("enough", "truthy"),), action="spread"),
    Rule(when=(), action="none"),
)
_POLICY_RULES = (
    Rule(when=(Pred("known", "truthy"),), action="keep"),
    Rule(when=(), action="default"),
)
_K_RULES = (
    Rule(when=(Pred("dynamic", "truthy"),), action="dynamic"),
    Rule(when=(), action="base"),
)
_SUBJECT_RULES = (
    Rule(when=(Pred("has_entity", "truthy"),), action="entity"),
    Rule(when=(), action="assertion"),
)
_JSON_LOAD_RULES = (
    Rule(when=(Pred("is_str", "truthy"),), action="parse"),
    Rule(when=(), action="keep"),
)
_DICT_RULES = (
    Rule(when=(Pred("is_dict", "truthy"),), action="ok"),
    Rule(when=(), action="empty"),
)
_RATING_RULES = (
    Rule(when=(Pred("has_rating", "truthy"),), action="blob"),
    Rule(when=(), action="default"),
)
_INFO_PROXY_RULES = (
    Rule(when=(Pred("need_proxy", "truthy"),), action="proxy"),
    Rule(when=(), action="keep"),
)
_INFO_APPEND_RULES = (
    Rule(when=(Pred("has_info", "truthy"),), action="include"),
    Rule(when=(), action="omit"),
)
_SEED_RULES = (
    Rule(when=(Pred("exists", "truthy"),), action="skip"),
    Rule(when=(), action="seed"),
)
_OUTCOME_RULES = (
    Rule(when=(Pred("correct", "truthy"),), action="hit"),
    Rule(when=(), action="miss"),
)


@dataclass(frozen=True)
class EloUpdate:
    ability: float
    difficulty: float
    expected: float


@dataclass(frozen=True)
class CalibrationVerdict:
    ability: float
    difficulty: float
    expected: float
    ability_n: int
    difficulty_n: int
    ability_se: float
    k_learner: float
    k_item: float
    policy: str = DEFAULT_CALIBRATION_POLICY
    policy_version: str = CALIBRATION_VERSION
    info: float = 0.0


@dataclass(frozen=True)
class BirthDifficultyVerdict:
    """Cold-start item difficulty prior (logit scale). Outcomes override later."""

    difficulty: float
    policy: str = BIRTH_PRIOR_POLICY
    policy_version: str = CALIBRATION_VERSION
    clamp: float = PRIOR_CLAMP


def estimate_birth_difficulty(
    mcq: dict,
    *,
    policy: str | None = None,
) -> float:
    """Estimate item difficulty (logit) from MCQ features. Compat float wrapper."""
    return birth_difficulty_prior(mcq, policy=policy).difficulty


def _option_spread_score(options: list[str]) -> float:
    lengths = [len(o) for o in options]
    mean = sum(lengths) / len(lengths)
    return apply(
        first_match(_SPREAD_RULES, {"mean_ok": mean > 0}).action,
        {
            "spread": lambda: max(
                -0.5, min(0.5, 0.6 - (max(lengths) - min(lengths)) / mean)
            ),
            "zero": lambda: 0.0,
        },
    )


def birth_difficulty_prior(
    mcq: dict,
    *,
    policy: str | None = None,
) -> BirthDifficultyVerdict:
    """Named Calibration sub-policy for cook-time difficulty seeding.

    Combines modest a-priori weights (not tuned to any sample):

      1. cognitive level - higher-order verbs harder than recall;
      2. guess rate - more options lower the floor (centered at 4);
      3. distractor plausibility - homogeneous option lengths harder;
      4. stem complexity - longer / multi-clause stems slightly harder.

    Returns clamped logit in ``[-PRIOR_CLAMP, PRIOR_CLAMP]``, 0 ≈ average item.
    """
    pol = (policy or BIRTH_PRIOR_POLICY).strip().lower() or BIRTH_PRIOR_POLICY
    question = str(mcq.get("question") or mcq.get("stem") or "")
    options = [
        str(o)
        for o in filter(
            lambda o: str(o).strip(),
            mcq.get("options") or mcq.get("choices") or [],
        )
    ]
    angle = str(mcq.get("cognitive_angle") or "")
    haystack = f"{angle}\n{question}"

    score = 0.0
    score += float(first_match(_HARD_VERB_RULES, {"hit": bool(_HARD_VERBS.search(haystack))}).extras["delta"])
    score += float(first_match(_EASY_VERB_RULES, {"hit": bool(_EASY_VERBS.search(haystack))}).extras["delta"])
    score += apply(
        first_match(_OPTION_COUNT_RULES, {"has_options": bool(options)}).action,
        {
            "guess_rate": lambda: (len(options) - 4) * 0.25,
            "none": lambda: 0.0,
        },
    )
    score += apply(
        first_match(_OPTION_SPREAD_RULES, {"enough": len(options) >= 3}).action,
        {
            "spread": lambda: _option_spread_score(options),
            "none": lambda: 0.0,
        },
    )
    words = len(question.split())
    score += max(-0.3, min(0.5, (words - 18) / 40.0))
    difficulty = max(-PRIOR_CLAMP, min(PRIOR_CLAMP, score))
    return BirthDifficultyVerdict(difficulty=difficulty, policy=pol)


def expected_correct(
    ability: float, difficulty: float, *, scale: float = ELO_SCALE
) -> float:
    return 1.0 / (1.0 + math.exp(-(ability - difficulty) / scale))


def fisher_info_1pl(ability: float, difficulty: float, *, scale: float = ELO_SCALE) -> float:
    p = expected_correct(ability, difficulty, scale=scale)
    return p * (1.0 - p)


def dynamic_k(n: int, k_base: float, *, boost: float = K_COLD_BOOST) -> float:
    """Larger step while evidence is thin (cold-start), shrinks toward k_base."""
    n = max(0, int(n))
    return float(k_base) * (1.0 + float(boost) / (1.0 + n))


def se_from_info(info: float) -> float:
    return 1.0 / math.sqrt(max(float(info), INFO_EPS))


def elo_update(
    ability: float,
    difficulty: float,
    correct: bool,
    *,
    k_learner: float = K_LEARNER,
    k_item: float = K_ITEM,
    scale: float = ELO_SCALE,
) -> EloUpdate:
    p = expected_correct(ability, difficulty, scale=scale)
    outcome = apply(
        first_match(_OUTCOME_RULES, {"correct": correct}).action,
        {"hit": lambda: 1.0, "miss": lambda: 0.0},
    )
    return EloUpdate(
        ability=ability + k_learner * (outcome - p),
        difficulty=difficulty + k_item * (p - outcome),
        expected=p,
    )


def update_from_outcome(
    ability: float,
    difficulty: float,
    correct: bool,
    *,
    ability_n: int = 0,
    difficulty_n: int = 0,
    running_info: float = 0.0,
    policy: str = DEFAULT_CALIBRATION_POLICY,
    use_dynamic_k: bool = True,
) -> CalibrationVerdict:
    """Pure calibration step. No I/O."""
    pol = (policy or DEFAULT_CALIBRATION_POLICY).strip().lower()
    pol = apply(
        first_match(_POLICY_RULES, {"known": pol in ("elo_online_v1", "elo")}).action,
        {"keep": lambda: pol, "default": lambda: DEFAULT_CALIBRATION_POLICY},
    )
    k_l = apply(
        first_match(_K_RULES, {"dynamic": use_dynamic_k}).action,
        {
            "dynamic": lambda: dynamic_k(ability_n, K_LEARNER),
            "base": lambda: K_LEARNER,
        },
    )
    k_i = apply(
        first_match(_K_RULES, {"dynamic": use_dynamic_k}).action,
        {
            "dynamic": lambda: dynamic_k(difficulty_n, K_ITEM),
            "base": lambda: K_ITEM,
        },
    )
    upd = elo_update(ability, difficulty, correct, k_learner=k_l, k_item=k_i)
    info = float(running_info) + fisher_info_1pl(ability, difficulty)
    return CalibrationVerdict(
        ability=upd.ability,
        difficulty=upd.difficulty,
        expected=upd.expected,
        ability_n=int(ability_n) + 1,
        difficulty_n=int(difficulty_n) + 1,
        ability_se=se_from_info(info),
        k_learner=k_l,
        k_item=k_i,
        policy=pol,
        info=info,
    )


def _latest_rating_blob(
    db: Session,
    type_uri: str,
    *,
    entity_id: uuid.UUID | None = None,
    assertion_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    subject = apply(
        first_match(_SUBJECT_RULES, {"has_entity": entity_id is not None}).action,
        {
            "entity": lambda: ("subject_entity_id", entity_id),
            "assertion": lambda: ("subject_assertion_id", assertion_id),
        },
    )
    column, subject_id = subject
    value = db.execute(
        text(
            f"""
            SELECT value FROM intel.projection
            WHERE type_concept_id = :t AND {column} = :s
            ORDER BY as_of DESC
            LIMIT 1
            """
        ),
        {"t": concept_id(db, type_uri), "s": subject_id},
    ).scalar()
    loaded = apply(
        first_match(_JSON_LOAD_RULES, {"is_str": isinstance(value, str)}).action,
        {"parse": lambda: json.loads(value), "keep": lambda: value},
    )
    return apply(
        first_match(_DICT_RULES, {"is_dict": isinstance(loaded, dict)}).action,
        {"ok": lambda: loaded, "empty": lambda: {}},
    )


def _latest_rating(
    db: Session,
    type_uri: str,
    *,
    entity_id: uuid.UUID | None = None,
    assertion_id: uuid.UUID | None = None,
) -> tuple[float, int]:
    blob = _latest_rating_blob(
        db, type_uri, entity_id=entity_id, assertion_id=assertion_id
    )
    return apply(
        first_match(_RATING_RULES, {"has_rating": blob.get("rating") is not None}).action,
        {
            "blob": lambda: (float(blob["rating"]), int(blob.get("n") or 0)),
            "default": lambda: (DEFAULT_RATING, 0),
        },
    )


def get_ability(db: Session, entity_id: uuid.UUID) -> tuple[float, int, float]:
    """Return (rating, n, se) for Selection / Mastery."""
    blob = _latest_rating_blob(db, ABILITY_PROJECTION_URI, entity_id=entity_id)

    def _from_blob() -> tuple[float, int, float]:
        info = float(blob.get("info") or 0.0)
        n = int(blob.get("n") or 0)
        info = apply(
            first_match(
                _INFO_PROXY_RULES,
                {"need_proxy": info <= 0 and n > 0},
            ).action,
            {"proxy": lambda: 0.2 * n, "keep": lambda: info},
        )
        return float(blob["rating"]), n, se_from_info(info)

    return apply(
        first_match(_RATING_RULES, {"has_rating": blob.get("rating") is not None}).action,
        {
            "default": lambda: (DEFAULT_RATING, 0, se_from_info(0.0)),
            "blob": _from_blob,
        },
    )


def get_difficulty(db: Session, assertion_id: uuid.UUID) -> tuple[float, int]:
    return _latest_rating(db, DIFFICULTY_PROJECTION_URI, assertion_id=assertion_id)


def _append_rating(
    db: Session,
    type_uri: str,
    *,
    now: datetime,
    rating: float,
    n: int,
    entity_id: uuid.UUID | None = None,
    assertion_id: uuid.UUID | None = None,
    info: float | None = None,
) -> None:
    payload: dict[str, Any] = {
        "rating": round(rating, 6),
        "n": n,
        "metric": "elo",
        "scale": ELO_SCALE,
        "policy": DEFAULT_CALIBRATION_POLICY,
        "version": CALIBRATION_VERSION,
    }
    payload.update(
        apply(
            first_match(_INFO_APPEND_RULES, {"has_info": info is not None}).action,
            {
                "include": lambda: {"info": round(float(info), 6)},
                "omit": lambda: {},
            },
        )
    )
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
            "value": json.dumps(payload),
        },
    )


def seed_item_difficulty(db: Session, assertion_id: uuid.UUID, difficulty: float) -> bool:
    existing = db.execute(
        text(
            """
            SELECT 1 FROM intel.projection
            WHERE type_concept_id = :t AND subject_assertion_id = :a
            LIMIT 1
            """
        ),
        {"t": concept_id(db, DIFFICULTY_PROJECTION_URI), "a": assertion_id},
    ).first()

    def _seed() -> bool:
        _append_rating(
            db,
            DIFFICULTY_PROJECTION_URI,
            now=datetime.now(timezone.utc),
            rating=difficulty,
            n=0,
            assertion_id=assertion_id,
        )
        return True

    return apply(
        first_match(_SEED_RULES, {"exists": existing is not None}).action,
        {"skip": lambda: False, "seed": _seed},
    )


def record_outcome(
    db: Session,
    *,
    subject_entity_id: uuid.UUID,
    assertion_id: uuid.UUID,
    correct: bool,
) -> EloUpdate:
    """Apply one calibration step and append ability + difficulty projections.

    Returns EloUpdate for backward compatibility with answer_signal / progress mirror.
    """
    ability_blob = _latest_rating_blob(
        db, ABILITY_PROJECTION_URI, entity_id=subject_entity_id
    )
    ability = apply(
        first_match(_RATING_RULES, {"has_rating": ability_blob.get("rating") is not None}).action,
        {
            "blob": lambda: float(ability_blob["rating"]),
            "default": lambda: DEFAULT_RATING,
        },
    )
    ability_n = int(ability_blob.get("n") or 0)
    running_info = float(ability_blob.get("info") or 0.0)
    difficulty, difficulty_n = _latest_rating(
        db, DIFFICULTY_PROJECTION_URI, assertion_id=assertion_id
    )
    verdict = update_from_outcome(
        ability,
        difficulty,
        correct,
        ability_n=ability_n,
        difficulty_n=difficulty_n,
        running_info=running_info,
    )
    now = datetime.now(timezone.utc)
    _append_rating(
        db,
        ABILITY_PROJECTION_URI,
        now=now,
        rating=verdict.ability,
        n=verdict.ability_n,
        entity_id=subject_entity_id,
        info=verdict.info,
    )
    _append_rating(
        db,
        DIFFICULTY_PROJECTION_URI,
        now=now,
        rating=verdict.difficulty,
        n=verdict.difficulty_n,
        assertion_id=assertion_id,
    )
    return EloUpdate(
        ability=verdict.ability, difficulty=verdict.difficulty, expected=verdict.expected
    )
