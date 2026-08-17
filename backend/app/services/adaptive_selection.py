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

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick

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

_REASON_RULES = (
    Rule(when=(Pred("focus", "truthy"),), action="focus concept"),
    Rule(when=(Pred("revisit", "truthy"),), action="spaced revisit"),
    Rule(when=(Pred("lineage", "truthy"),), action="follows your last question"),
    Rule(when=(Pred("page", "truthy"),), action="new page"),
    Rule(when=(Pred("level", "truthy"),), action="right at your level"),
    Rule(when=(Pred("reinforce", "truthy"),), action="reinforces a recent miss"),
    Rule(when=(), action="in order through this page"),
)

_LINEAGE_RULES = (
    Rule(when=(Pred("correct_true", "truthy"),), action=LINK_HARDER_THAN),
    Rule(when=(Pred("correct_false", "truthy"),), action=LINK_FOLLOW_UP_AFTER_MISS),
    Rule(when=(), action="none"),
)

_SELECT_RULES = (
    Rule(when=(Pred("single_or_seq", "truthy"),), action="sequence_or_single"),
    Rule(when=(Pred("pol", "eq", "concept_reinforce"),), action="concept_reinforce"),
    Rule(when=(Pred("pol", "eq", "difficulty_edge"),), action="difficulty_edge"),
    Rule(when=(Pred("pol", "eq", "adaptive_v1"),), action="adaptive_v1"),
    Rule(when=(), action="unknown"),
)

_ADAPTIVE_REASON_RULES = (
    Rule(when=(Pred("lineage", "truthy"),), action="adaptive_v1:lineage"),
    Rule(when=(Pred("mastery", "truthy"),), action="adaptive_v1:mastery_reinforce"),
    Rule(when=(), action="adaptive_v1:scored"),
)


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
    hit = first_match(
        _REASON_RULES,
        {
            "focus": "focus" in r or "mastery_reinforce" in r,
            "revisit": "revisit" in r or "due" in r or "spaced" in r,
            "lineage": "lineage" in r,
            "page": "new_page" in r or "page" in r,
            "level": "difficulty" in r or "edge" in r,
            "reinforce": "reinforce" in r,
        },
    )
    return hit.action


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
    return choose(pol == "sequence", "classic", "adaptive")


def persist_study_mode(mode: str) -> str:
    """UI Adaptive/Classic to stored selection_policy."""
    return choose(str(mode).strip().lower() == "classic", "sequence", "adaptive_v1")


def _due_map_from(due_raw: Any) -> dict[str, float] | None:
    return pick(
        isinstance(due_raw, dict) and bool(due_raw),
        lambda: {
            str(k): float(v)
            for k, v in filter(
                lambda kv: kv[0] is not None and str(kv[0]).strip(),
                due_raw.items(),
            )
        },
        lambda: None,
    )


def build_learner_state(progress: Mapping[str, Any]) -> LearnerState:
    """Read the learner's latest confirmed answer + calibrated ability from progress."""
    last = progress.get("last_confirmed_answer") or {}
    correct = last.get("correct")
    ability = progress.get("learner_ability")
    concept_ability = progress.get("concept_ability")
    focus = str(progress.get("focus_concept") or "").strip() or None
    due_raw = progress.get("concept_revisit_hours") or {}
    return LearnerState(
        last_concept_key=(last.get("concept_key") or None),
        last_correct=pick(correct is not None, lambda: bool(correct), lambda: None),
        last_assertion_id=pick(
            bool(last.get("assertion_id")),
            lambda: str(last["assertion_id"]),
            lambda: None,
        ),
        ability=pick(ability is not None, lambda: float(ability), lambda: None),
        concept_ability=pick(
            isinstance(concept_ability, dict) and bool(concept_ability),
            lambda: {str(k): float(v) for k, v in concept_ability.items()},
            lambda: None,
        ),
        focus_concept=focus,
        mastery_stop=bool(progress.get("mastery_stop")),
        concept_revisit_hours=_due_map_from(due_raw),
    )


def concept_text_matches(needle: str, *, key: str | None, label: str | None) -> bool:
    """Focus / due-map match: substring on label or key (case-insensitive)."""
    n = (needle or "").strip().lower()
    lab = (label or "").strip().lower()
    k = (key or "").strip().lower()
    return bool(n) and (n in lab or n in k or lab == n)


def _matching_ids(
    rows: Sequence[CandidateSignals], needle: str
) -> list[CandidateSignals]:
    return list(
        filter(
            lambda c: concept_text_matches(needle, key=c.concept_key, label=c.concept_label),
            rows,
        )
    )


def _due_preferred(
    pool: list[CandidateSignals], due_map: Mapping[str, float]
) -> list[CandidateSignals]:
    due_sorted = sorted(due_map.items(), key=lambda kv: kv[1])
    matches = list(
        filter(None, map(lambda kv: _matching_ids(pool, kv[0]), due_sorted[:3]))
    )
    return pick(bool(matches), lambda: matches[0], lambda: pool)


def narrow_serve_pool(
    candidates: Sequence[CandidateSignals],
    state: LearnerState,
) -> list[CandidateSignals]:
    """Apply focus → mastery diversify → spaced due prefer. Soft: never empty the bank."""
    pool = list(candidates)

    def _after_focus(current: list[CandidateSignals], focus: str) -> list[CandidateSignals]:
        stop_concept = (state.last_concept_key or "").strip()
        diversified = list(
            filter(
                lambda c: (c.concept_key or "").strip() != stop_concept,
                current,
            )
        )
        narrowed = pick(
            bool(state.mastery_stop and stop_concept and diversified),
            lambda: diversified,
            lambda: current,
        )
        due_map = state.concept_revisit_hours
        return pick(
            bool(due_map) and not bool(focus),
            lambda: _due_preferred(narrowed, due_map),
            lambda: narrowed,
        )

    def _with_pool() -> list[CandidateSignals]:
        focus = (state.focus_concept or "").strip()
        preferred = pick(bool(focus), lambda: _matching_ids(pool, focus), lambda: [])
        return pick(bool(preferred), lambda: preferred, lambda: _after_focus(pool, focus))

    return pick(not pool, lambda: pool, _with_pool)


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
    per_concept = pick(
        bool(concept) and state.concept_ability is not None,
        lambda: state.concept_ability.get(concept),
        lambda: None,
    )
    return pick(
        per_concept is not None,
        lambda: per_concept,
        lambda: choose(state.ability is not None, state.ability, 0.0),
    )


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
    return [
        CandidateSignals(
            assertion_id=cid,
            difficulty=diffs.get(cid),
            concept_key=concepts.get(cid),
            concept_label=labels.get(cid),
            lineage_kind=lineage.get(cid),
            exposure=int(exposure.get(cid, 0) or 0),
        )
        for cid in candidate_ids
    ]


def _lineage_want(state: LearnerState) -> str | None:
    hit = first_match(
        _LINEAGE_RULES,
        {
            "correct_true": state.last_correct is True,
            "correct_false": state.last_correct is False,
        },
    )
    return choose(hit.action == "none", None, hit.action)


def _apply_lineage_filter(
    candidates: Sequence[CandidateSignals], state: LearnerState
) -> list[CandidateSignals]:
    want = _lineage_want(state)
    routed = list(filter(lambda c: c.lineage_kind == want, candidates))
    return pick(
        not want,
        lambda: list(candidates),
        lambda: choose(bool(routed), routed, list(candidates)),
    )


def _nearest_to_band(
    candidates: Sequence[CandidateSignals],
    state: LearnerState,
    target_success: float,
) -> str:
    def _dist(c: CandidateSignals) -> float:
        ability = _ability_for_concept(state, c.concept_key)
        target = target_difficulty(ability, target_success)
        difficulty = choose(c.difficulty is not None, c.difficulty, ability)
        difficulty = choose(math.isfinite(difficulty), difficulty, ability)
        return abs(difficulty - target)

    return min(candidates, key=_dist).assertion_id


def _first_id(
    candidates: Sequence[CandidateSignals], pred
) -> str:
    found = next(filter(pred, candidates), None)
    return pick(
        found is None,
        lambda: candidates[0].assertion_id,
        lambda: found.assertion_id,
    )


def _concept_reinforce_pick(
    candidates: Sequence[CandidateSignals], state: LearnerState
) -> str:
    last = state.last_concept_key
    return pick(
        not last or state.last_correct is None,
        lambda: candidates[0].assertion_id,
        lambda: pick(
            state.last_correct is False,
            lambda: _first_id(candidates, lambda c: c.concept_key == last),
            lambda: _first_id(
                candidates, lambda c: bool(c.concept_key) and c.concept_key != last
            ),
        ),
    )


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
    difficulty = choose(c.difficulty is not None, c.difficulty, ability)
    p = expected_correct(ability, difficulty)

    # Band fit: peak at target_success (Learn practice), not at 0.5.
    band_fit = max(0.0, 1.0 - abs(p - target_success) / max(target_success, 1.0 - target_success))

    information = fisher_information_1pl(ability, difficulty) / 0.25  # normalize; max P(1-P)=0.25

    # Mastery need: lower concept ability → higher need; miss on concept boosts.
    concept_ab = ability
    mastery_need = 1.0 / (1.0 + math.exp(concept_ab))  # logistic: high when weak
    mastery_need = choose(
        bool(prefer_concept) and c.concept_key == prefer_concept,
        min(1.0, mastery_need + 0.35),
        mastery_need,
    )

    novelty = 1.0 / (1.0 + max(0, c.exposure))

    want = _lineage_want(state)
    lineage = choose(bool(want) and c.lineage_kind == want, 1.0, 0.0)

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
    prefer_concept = choose(
        state.last_correct is False and bool(state.last_concept_key) and not lineage_hit,
        state.last_concept_key,
        None,
    )

    def _cold() -> SelectionVerdict:
        return pick(
            bool(prefer_concept),
            lambda: SelectionVerdict(
                assertion_id=_concept_reinforce_pick(pool, state),
                rationale="adaptive_v1:cold_bank_concept_reinforce",
                policy="adaptive_v1",
            ),
            lambda: SelectionVerdict(
                assertion_id=pool[0].assertion_id,
                rationale="adaptive_v1:cold_bank_sequence",
                policy="adaptive_v1",
            ),
        )

    def _scored() -> SelectionVerdict:
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
        reason = first_match(
            _ADAPTIVE_REASON_RULES,
            {
                "lineage": lineage_hit and best_s.lineage >= 1.0,
                "mastery": bool(prefer_concept) and best_c.concept_key == prefer_concept,
            },
        ).action
        return SelectionVerdict(
            assertion_id=best_c.assertion_id,
            rationale=reason,
            policy="adaptive_v1",
            scores=best_s,
            ranked_top=top,
            details={"mode": mode, "prefer_concept": prefer_concept},
        )

    return pick(
        not any(c.difficulty is not None for c in pool),
        _cold,
        _scored,
    )


def _difficulty_edge_verdict(
    rows: list[CandidateSignals],
    state: LearnerState,
    pol: str,
    target_success: float,
) -> SelectionVerdict:
    pool = _apply_lineage_filter(rows, state)
    return pick(
        not any(c.difficulty is not None for c in pool),
        lambda: SelectionVerdict(
            assertion_id=rows[0].assertion_id,
            rationale="difficulty_edge:cold_sequence",
            policy=pol,
        ),
        lambda: SelectionVerdict(
            assertion_id=_nearest_to_band(pool, state, target_success),
            rationale="difficulty_edge:band",
            policy=pol,
        ),
    )


def _select_policy(
    rows: list[CandidateSignals],
    state: LearnerState,
    pol: str,
    orig_policy: str | None,
    mode: ServeMode,
    target_success: float,
) -> SelectionVerdict:
    effective = choose(
        (
            pol == "difficulty_edge"
            and state.last_correct is False
            and bool(state.last_concept_key)
            and not any(c.lineage_kind for c in rows)
        ),
        "concept_reinforce",
        pol,
    )
    hit = first_match(
        _SELECT_RULES,
        {
            "single_or_seq": len(rows) == 1 or effective == "sequence",
            "pol": effective,
        },
    )
    return apply(
        hit.action,
        {
            "sequence_or_single": lambda: SelectionVerdict(
                assertion_id=rows[0].assertion_id,
                rationale=choose(effective == "sequence", "sequence", "single_candidate"),
                policy=choose(
                    effective == "sequence", "sequence", normalize_policy(orig_policy)
                ),
            ),
            "concept_reinforce": lambda: SelectionVerdict(
                assertion_id=_concept_reinforce_pick(rows, state),
                rationale="concept_reinforce",
                policy=effective,
            ),
            "difficulty_edge": lambda: _difficulty_edge_verdict(
                rows, state, effective, target_success
            ),
            "adaptive_v1": lambda: _adaptive_pick(
                rows, state, mode=mode, target_success=target_success
            ),
            "unknown": lambda: SelectionVerdict(
                assertion_id=rows[0].assertion_id,
                rationale="unknown_policy_sequence",
                policy=effective,
            ),
        },
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

    def _from_candidates() -> SelectionVerdict:
        rows = pick(
            isinstance(candidates[0], CandidateSignals),
            lambda: list(candidates),
            lambda: _candidates_from_maps(
                [str(x) for x in candidates],
                concept_by_id,
                difficulty_by_id,
                lineage_by_id,
                exposure_by_id,
                concept_label_by_id,
            ),
        )
        rows = narrow_serve_pool(rows, state)
        return pick(
            not rows,
            lambda: SelectionVerdict(assertion_id=None, rationale="empty", policy=pol),
            lambda: _select_policy(rows, state, pol, policy, mode, target_success),
        )

    return pick(
        not candidates,
        lambda: SelectionVerdict(assertion_id=None, rationale="empty", policy=pol),
        _from_candidates,
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
