"""Adaptive Selection Engine - pure next-item policy (ADR 0004 seam).

Design: docs/ADAPTIVE_SELECTION_ENGINE.md
Version: qb.selection.v1

Deterministic scoring over learner state + candidate bank. No LLM. Callers
(load DB signals in question_pool) depend on ``select_next``, not ad-hoc ranking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

SELECTION_VERSION = "qb.selection.v1"
DEFAULT_SELECTION_POLICY = "adaptive_v1"

# Aim each Learn question at the productive-struggle band: serve the item this
# learner is expected to get right about TARGET_SUCCESS of the time. On the
# shared logit scale (same as the calibrator), that is an item a little *below*
# ability: desirable difficulty (Bjork); mostly succeeding, genuinely stretched.
TARGET_SUCCESS = 0.75

LINK_FOLLOW_UP_AFTER_MISS = "follow_up_after_miss"
LINK_HARDER_THAN = "harder_than"

ServeMode = Literal["learn", "test"]

# Mode weight profiles (sum = 1.0). Learn = Elo practice; Test = CAT-leaning.
_WEIGHTS: dict[str, dict[str, float]] = {
    "learn": {
        "band_fit": 0.40,
        "mastery_need": 0.25,
        "information": 0.15,
        "novelty": 0.10,
        "lineage": 0.10,
    },
    "test": {
        "information": 0.40,
        "band_fit": 0.20,
        "mastery_need": 0.15,
        "novelty": 0.15,
        "lineage": 0.10,
    },
}

_POLICY_ALIASES = {
    "adaptive": "adaptive_v1",
}


@dataclass(frozen=True)
class LearnerState:
    """What the loop listens to: last answer + calibrated ability + serve hygiene.

    Orchestration loads these from progress; ``select_next`` owns focus / mastery
    diversify / spaced prefer (holy grail — not pre-filtered in question_pool).
    """

    last_concept_key: str | None = None
    last_correct: bool | None = None
    last_assertion_id: str | None = None
    ability: float | None = None
    concept_ability: dict[str, float] | None = None
    focus_concept: str | None = None
    mastery_stop: bool = False
    concept_revisit_hours: Mapping[str, float] | None = None


@dataclass(frozen=True)
class CandidateSignals:
    """Per-candidate evidence the scorer may see. Missing pieces are None/0."""

    assertion_id: str
    difficulty: float | None = None
    concept_key: str | None = None
    concept_label: str | None = None
    lineage_kind: str | None = None
    exposure: int = 0


@dataclass(frozen=True)
class SelectionScores:
    band_fit: float
    information: float
    mastery_need: float
    novelty: float
    lineage: float
    overall: float


@dataclass(frozen=True)
class SelectionVerdict:
    assertion_id: str | None
    rationale: str
    policy: str
    scores: SelectionScores | None = None
    policy_version: str = SELECTION_VERSION
    ranked_top: tuple[tuple[str, float], ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_SELECTION_POLICY).strip().lower()
    return _POLICY_ALIASES.get(p, p or DEFAULT_SELECTION_POLICY)


def label_selection_reason(rationale: str) -> str:
    """Map the engine's internal rationale to a short, learner-facing label."""
    r = (rationale or "").lower()
    if "focus" in r or "mastery_reinforce" in r:
        return "focus concept"
    if "revisit" in r or "due" in r or "spaced" in r:
        return "spaced revisit"
    if "lineage" in r:
        return "follows your last question"
    if "new_page" in r or "page" in r:
        return "new page"
    if "difficulty" in r or "edge" in r:
        return "right at your level"
    if "reinforce" in r:
        return "reinforces a recent miss"
    return "in order through this page"


@dataclass(frozen=True)
class SignalLoadPlan:
    load_concepts: bool
    load_difficulty: bool
    load_lineage: bool
    load_exposure: bool
    policy_version: str = SELECTION_VERSION


def plan_signal_load(*, policy: str, state: LearnerState) -> SignalLoadPlan:
    """Which DB signals orchestration must load before select_next."""
    pol = normalize_policy(policy)
    load_concepts = (
        bool(state.focus_concept)
        or state.mastery_stop
        or bool(state.concept_revisit_hours)
        or pol in ("adaptive_v1", "difficulty_edge", "concept_reinforce")
    )
    load_difficulty = pol in ("adaptive_v1", "difficulty_edge")
    return SignalLoadPlan(
        load_concepts=load_concepts,
        load_difficulty=load_difficulty,
        load_lineage=load_difficulty and bool(state.last_assertion_id),
        load_exposure=pol == "adaptive_v1",
    )


StudyMode = Literal["classic", "adaptive"]


def label_study_mode(policy: str | None) -> StudyMode:
    """Learner-facing Adaptive/Classic from a stored selection_policy."""
    pol = (policy or "").strip().lower()
    return "classic" if pol == "sequence" else "adaptive"


def persist_study_mode(mode: str) -> str:
    """UI Adaptive/Classic to stored selection_policy."""
    return "sequence" if str(mode).strip().lower() == "classic" else "adaptive_v1"


def build_learner_state(progress: Mapping[str, Any]) -> LearnerState:
    """Read the learner's latest confirmed answer + calibrated ability from progress."""
    last = progress.get("last_confirmed_answer") or {}
    correct = last.get("correct")
    ability = progress.get("learner_ability")
    concept_ability = progress.get("concept_ability")
    focus = str(progress.get("focus_concept") or "").strip() or None
    due_raw = progress.get("concept_revisit_hours") or {}
    due_map: dict[str, float] | None = None
    if isinstance(due_raw, dict) and due_raw:
        due_map = {
            str(k): float(v)
            for k, v in due_raw.items()
            if k is not None and str(k).strip()
        }
    return LearnerState(
        last_concept_key=(last.get("concept_key") or None),
        last_correct=bool(correct) if correct is not None else None,
        last_assertion_id=(str(last["assertion_id"]) if last.get("assertion_id") else None),
        ability=float(ability) if ability is not None else None,
        concept_ability=(
            {str(k): float(v) for k, v in concept_ability.items()}
            if isinstance(concept_ability, dict) and concept_ability
            else None
        ),
        focus_concept=focus,
        mastery_stop=bool(progress.get("mastery_stop")),
        concept_revisit_hours=due_map,
    )


def concept_text_matches(needle: str, *, key: str | None, label: str | None) -> bool:
    """Focus / due-map match: substring on label or key (case-insensitive)."""
    n = (needle or "").strip().lower()
    if not n:
        return False
    lab = (label or "").strip().lower()
    k = (key or "").strip().lower()
    return n in lab or n in k or lab == n


def _matching_ids(
    rows: Sequence[CandidateSignals], needle: str
) -> list[CandidateSignals]:
    return [
        c
        for c in rows
        if concept_text_matches(needle, key=c.concept_key, label=c.concept_label)
    ]


def narrow_serve_pool(
    candidates: Sequence[CandidateSignals],
    state: LearnerState,
) -> list[CandidateSignals]:
    """Apply focus → mastery diversify → spaced due prefer. Soft: never empty the bank."""
    pool = list(candidates)
    if not pool:
        return pool

    focus = (state.focus_concept or "").strip()
    if focus:
        preferred = _matching_ids(pool, focus)
        if preferred:
            return preferred

    if state.mastery_stop:
        stop_concept = (state.last_concept_key or "").strip()
        if stop_concept:
            diversified = [
                c
                for c in pool
                if (c.concept_key or "").strip() != stop_concept
            ]
            if diversified:
                pool = diversified

    due_map = state.concept_revisit_hours
    if due_map and not focus:
        due_sorted = sorted(due_map.items(), key=lambda kv: kv[1])
        for due_key, _hours in due_sorted[:3]:
            preferred = _matching_ids(pool, due_key)
            if preferred:
                return preferred
    return pool


def target_difficulty(ability: float, target_success: float = TARGET_SUCCESS) -> float:
    """Item difficulty at which ``ability`` yields ``target_success`` expected success.

    Inverts the logistic the calibrator assumes: P = 1/(1+e^-(ability-difficulty)),
    so difficulty = ability - logit(P).
    """
    p = min(max(target_success, 1e-6), 1.0 - 1e-6)
    return ability - math.log(p / (1.0 - p))


# Back-compat name used by older tests / sim.
_target_difficulty = target_difficulty


def expected_correct(ability: float, difficulty: float) -> float:
    """1PL success probability on the shared Elo logit scale."""
    return 1.0 / (1.0 + math.exp(-(ability - difficulty)))


def fisher_information_1pl(ability: float, difficulty: float) -> float:
    """Fisher information for a Rasch/1PL item at θ̂ (equals P(1-P))."""
    p = expected_correct(ability, difficulty)
    return p * (1.0 - p)


def _ability_for_concept(state: LearnerState, concept: str | None) -> float:
    if concept and state.concept_ability is not None:
        per_concept = state.concept_ability.get(concept)
        if per_concept is not None:
            return per_concept
    return state.ability if state.ability is not None else 0.0


def _candidates_from_maps(
    candidate_ids: Sequence[str],
    concept_by_id: Mapping[str, str | None] | None,
    difficulty_by_id: Mapping[str, float] | None,
    lineage_by_id: Mapping[str, str] | None,
    exposure_by_id: Mapping[str, int] | None,
    concept_label_by_id: Mapping[str, str | None] | None = None,
) -> list[CandidateSignals]:
    concepts = concept_by_id or {}
    labels = concept_label_by_id or {}
    diffs = difficulty_by_id or {}
    lineage = lineage_by_id or {}
    exposure = exposure_by_id or {}
    out: list[CandidateSignals] = []
    for cid in candidate_ids:
        out.append(
            CandidateSignals(
                assertion_id=cid,
                difficulty=diffs.get(cid),
                concept_key=concepts.get(cid),
                concept_label=labels.get(cid),
                lineage_kind=lineage.get(cid),
                exposure=int(exposure.get(cid, 0) or 0),
            )
        )
    return out


def _lineage_want(state: LearnerState) -> str | None:
    if state.last_correct is True:
        return LINK_HARDER_THAN
    if state.last_correct is False:
        return LINK_FOLLOW_UP_AFTER_MISS
    return None


def _apply_lineage_filter(
    candidates: Sequence[CandidateSignals], state: LearnerState
) -> list[CandidateSignals]:
    want = _lineage_want(state)
    if not want:
        return list(candidates)
    routed = [c for c in candidates if c.lineage_kind == want]
    return routed if routed else list(candidates)


def _nearest_to_band(
    candidates: Sequence[CandidateSignals],
    state: LearnerState,
    target_success: float,
) -> str:
    best_id = candidates[0].assertion_id
    best_dist: float | None = None
    for c in candidates:
        ability = _ability_for_concept(state, c.concept_key)
        target = target_difficulty(ability, target_success)
        difficulty = c.difficulty if c.difficulty is not None else ability
        if not math.isfinite(difficulty):
            difficulty = ability
        dist = abs(difficulty - target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_id = c.assertion_id
    return best_id


def _concept_reinforce_pick(
    candidates: Sequence[CandidateSignals], state: LearnerState
) -> str:
    last = state.last_concept_key
    if not last or state.last_correct is None:
        return candidates[0].assertion_id

    if state.last_correct is False:
        for c in candidates:
            if c.concept_key == last:
                return c.assertion_id
        return candidates[0].assertion_id

    for c in candidates:
        if c.concept_key and c.concept_key != last:
            return c.assertion_id
    return candidates[0].assertion_id


def _score_candidate(
    c: CandidateSignals,
    state: LearnerState,
    *,
    mode: ServeMode,
    target_success: float,
    prefer_concept: str | None,
) -> SelectionScores:
    weights = _WEIGHTS[mode]
    ability = _ability_for_concept(state, c.concept_key)
    difficulty = c.difficulty if c.difficulty is not None else ability
    p = expected_correct(ability, difficulty)

    # Band fit: peak at target_success (Learn practice), not at 0.5.
    band_fit = max(0.0, 1.0 - abs(p - target_success) / max(target_success, 1.0 - target_success))

    information = fisher_information_1pl(ability, difficulty) / 0.25  # normalize; max P(1-P)=0.25

    # Mastery need: lower concept ability → higher need; miss on concept boosts.
    concept_ab = ability
    mastery_need = 1.0 / (1.0 + math.exp(concept_ab))  # logistic: high when weak
    if prefer_concept and c.concept_key == prefer_concept:
        mastery_need = min(1.0, mastery_need + 0.35)

    novelty = 1.0 / (1.0 + max(0, c.exposure))

    want = _lineage_want(state)
    lineage = 1.0 if want and c.lineage_kind == want else 0.0

    overall = (
        weights["band_fit"] * band_fit
        + weights["information"] * min(1.0, information)
        + weights["mastery_need"] * mastery_need
        + weights["novelty"] * novelty
        + weights["lineage"] * lineage
    )
    return SelectionScores(
        band_fit=band_fit,
        information=min(1.0, information),
        mastery_need=mastery_need,
        novelty=novelty,
        lineage=lineage,
        overall=overall,
    )


def _adaptive_pick(
    candidates: Sequence[CandidateSignals],
    state: LearnerState,
    *,
    mode: ServeMode,
    target_success: float,
) -> SelectionVerdict:
    want = _lineage_want(state)
    lineage_hit = bool(want and any(c.lineage_kind == want for c in candidates))
    pool = _apply_lineage_filter(candidates, state)

    # Miss without a matching lineage edge → soft-prefer same concept (KT proxy).
    prefer_concept: str | None = None
    if state.last_correct is False and state.last_concept_key and not lineage_hit:
        prefer_concept = state.last_concept_key

    if not any(c.difficulty is not None for c in pool):
        if prefer_concept:
            pick = _concept_reinforce_pick(pool, state)
            return SelectionVerdict(
                assertion_id=pick,
                rationale="adaptive_v1:cold_bank_concept_reinforce",
                policy="adaptive_v1",
            )
        return SelectionVerdict(
            assertion_id=pool[0].assertion_id,
            rationale="adaptive_v1:cold_bank_sequence",
            policy="adaptive_v1",
        )

    scored: list[tuple[CandidateSignals, SelectionScores]] = [
        (
            c,
            _score_candidate(
                c,
                state,
                mode=mode,
                target_success=target_success,
                prefer_concept=prefer_concept,
            ),
        )
        for c in pool
    ]

    # Max overall; ties → earlier sequence index among original candidates.
    order_index = {c.assertion_id: i for i, c in enumerate(candidates)}

    def sort_key(item: tuple[CandidateSignals, SelectionScores]) -> tuple:
        c, s = item
        return (-s.overall, order_index.get(c.assertion_id, 10**9))

    scored.sort(key=sort_key)
    best_c, best_s = scored[0]
    top = tuple((c.assertion_id, s.overall) for c, s in scored[:5])
    reason = "adaptive_v1:scored"
    if lineage_hit and best_s.lineage >= 1.0:
        reason = "adaptive_v1:lineage"
    elif prefer_concept and best_c.concept_key == prefer_concept:
        reason = "adaptive_v1:mastery_reinforce"
    return SelectionVerdict(
        assertion_id=best_c.assertion_id,
        rationale=reason,
        policy="adaptive_v1",
        scores=best_s,
        ranked_top=top,
        details={"mode": mode, "prefer_concept": prefer_concept},
    )


def select_next(
    candidates: Sequence[CandidateSignals] | Sequence[str],
    state: LearnerState,
    *,
    policy: str | None = None,
    mode: ServeMode = "learn",
    target_success: float = TARGET_SUCCESS,
    concept_by_id: Mapping[str, str | None] | None = None,
    concept_label_by_id: Mapping[str, str | None] | None = None,
    difficulty_by_id: Mapping[str, float] | None = None,
    lineage_by_id: Mapping[str, str] | None = None,
    exposure_by_id: Mapping[str, int] | None = None,
) -> SelectionVerdict:
    """Pick the next assertion from the unanswered candidate bank.

    ``candidates`` may be ``CandidateSignals`` rows or bare id strings (then maps
    supply signals). Every policy degrades safely toward sequence order.
    Owns focus / mastery diversify / spaced prefer and difficulty_edge→reinforce.
    """
    pol = normalize_policy(policy)
    if not candidates:
        return SelectionVerdict(
            assertion_id=None, rationale="empty", policy=pol
        )

    if candidates and isinstance(candidates[0], CandidateSignals):
        rows = list(candidates)  # type: ignore[arg-type]
    else:
        ids = [str(x) for x in candidates]  # type: ignore[arg-type]
        rows = _candidates_from_maps(
            ids,
            concept_by_id,
            difficulty_by_id,
            lineage_by_id,
            exposure_by_id,
            concept_label_by_id,
        )

    rows = narrow_serve_pool(rows, state)
    if not rows:
        return SelectionVerdict(assertion_id=None, rationale="empty", policy=pol)

    # Cold lineage on legacy band policy: miss → reinforce same concept.
    if (
        pol == "difficulty_edge"
        and state.last_correct is False
        and state.last_concept_key
        and not any(c.lineage_kind for c in rows)
    ):
        pol = "concept_reinforce"

    if len(rows) == 1 or pol == "sequence":
        return SelectionVerdict(
            assertion_id=rows[0].assertion_id,
            rationale="sequence" if pol == "sequence" else "single_candidate",
            policy=pol if pol == "sequence" else normalize_policy(policy),
        )

    if pol == "concept_reinforce":
        pick = _concept_reinforce_pick(rows, state)
        return SelectionVerdict(
            assertion_id=pick,
            rationale="concept_reinforce",
            policy=pol,
        )

    if pol == "difficulty_edge":
        pool = _apply_lineage_filter(rows, state)
        if not any(c.difficulty is not None for c in pool):
            return SelectionVerdict(
                assertion_id=rows[0].assertion_id,
                rationale="difficulty_edge:cold_sequence",
                policy=pol,
            )
        pick = _nearest_to_band(pool, state, target_success)
        return SelectionVerdict(
            assertion_id=pick,
            rationale="difficulty_edge:band",
            policy=pol,
        )

    if pol == "adaptive_v1":
        return _adaptive_pick(rows, state, mode=mode, target_success=target_success)

    # Unknown policy → safe sequence.
    return SelectionVerdict(
        assertion_id=rows[0].assertion_id,
        rationale="unknown_policy_sequence",
        policy=pol,
    )


def choose_next_assertion(
    policy: str,
    candidates: list[str],
    concept_by_id: dict[str, str | None],
    state: LearnerState,
    difficulty_by_id: dict[str, float] | None = None,
    lineage_by_id: dict[str, str] | None = None,
    *,
    exposure_by_id: dict[str, int] | None = None,
    mode: ServeMode = "learn",
) -> str | None:
    """Backward-compatible seam: returns assertion id only (ADR 0004 callers)."""
    verdict = select_next(
        candidates,
        state,
        policy=policy,
        mode=mode,
        concept_by_id=concept_by_id,
        difficulty_by_id=difficulty_by_id,
        lineage_by_id=lineage_by_id,
        exposure_by_id=exposure_by_id,
    )
    return verdict.assertion_id
