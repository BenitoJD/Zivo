"""Quality Evaluation Engine — pure decision core (policy seam).

Design: docs/QUALITY_EVALUATION_ENGINE.md
Version: qb.quality.v1

Deterministic composition of heuristic / verify / critic / similarity / CTT
signals into pass|fail|revise. LLM I/O stays in mcq_quality; this module owns
the boolean and the scores. Callers depend on this seam (ADR 0004), not on
ad-hoc if-ladders.
"""

from __future__ import annotations

import os as _os
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.mcq_heuristics import (
    FATAL_FLAW_CODES,
    has_fatal_heuristic_flaws,
    run_heuristic_checks,
)

QUALITY_VERSION = "qb.quality.v1"
DEFAULT_QUALITY_POLICY = "code_driven_v1"

# Stem/answer embedding cosine cutoff vs prior MCQs on the same page.
MCQ_SIMILARITY_THRESHOLD = 0.92
# Critic spend rate when no heuristic flaws force a call (env can still override at cook).
DEFAULT_CRITIC_SAMPLE_RATE = 1.0
# Serial cook rewrite/regenerate budget (orchestration reads this; loop stays plumbing).
MAX_GENERATION_ATTEMPTS = 3
# Batch cook knobs (env overrides stay on this facade, not in mcq_quality).
GENERATION_CONCURRENCY = max(1, int(_os.getenv("ZIVO_GENERATION_CONCURRENCY", "4")))
PIPELINE_DRAFT_SPLIT = _os.getenv("ZIVO_PIPELINE_DRAFT_SPLIT", "1").lower() not in (
    "0",
    "false",
    "no",
)
CRITIC_SAMPLE_RATE = float(
    _os.getenv("ZIVO_CRITIC_SAMPLE_RATE", str(DEFAULT_CRITIC_SAMPLE_RATE))
)
DEFAULT_CANDIDATES_PER_ASPECT = 2
PRIOR_MCQ_PROMPT_ITEMS = 6

Decision = Literal["pass", "fail", "revise"]
Stage = Literal["cook", "empirical"]

# Structural / key fatals that should not burn a rewrite slot (regenerate instead).
NO_REWRITE_CODES = frozenset(
    {
        "invalid_structure",
        "wrong_answer_key",
        "more_than_one_correct",
        "not_grounded",
        "too_similar_to_prior",
    }
)

# Soft structural cues that affect distractor/clarity scores but are not always fatal.
SOFT_DISTRACTOR_CODES = frozenset(
    {
        "implausible_distractors",
        "none_or_all_of_above",
        "longest_option_correct",
        "grammatical_cues",
    }
)
SOFT_CLARITY_CODES = frozenset(
    {
        "ambiguous_unclear",
        "unfocused_stem",
        "negative_wording",
        "not_self_contained",
        "meta_page_reference",
        "recognition_only",
    }
)

# CTT defaults (aligned with item_retirement settings / Assessment Systems practice).
EMPIRICAL_MIN_EXPOSURE = 12
EMPIRICAL_MAX_BROKEN_RATE = 0.08
EMPIRICAL_MIN_RPBIS = 0.20
EMPIRICAL_EASY_P = 0.95
EMPIRICAL_HARD_P = 0.10

_STRUCTURE_FATAL = FATAL_FLAW_CODES & {
    "invalid_structure",
    "none_or_all_of_above",
    "negative_wording",
    "longest_option_correct",
    "unfocused_stem",
    "not_self_contained",
    "meta_page_reference",
    "too_similar_to_prior",
}
_KEY_BAD = {"wrong_answer_key", "more_than_one_correct", "not_grounded"}

_DECIDE_RULES = (
    Rule(when=(Pred("too_similar", "truthy"),), action="fail", extras={"reason": "too_similar"}),
    Rule(when=(Pred("has_verify_flaw", "truthy"),), action="fail", extras={"reason": "verify"}),
    Rule(
        when=(
            Pred("fatal", "truthy"),
            Pred("can_rewrite_cook", "truthy"),
            Pred("hard_no_rewrite", "falsey"),
        ),
        action="revise",
        extras={"reason": "fatal"},
    ),
    Rule(when=(Pred("fatal", "truthy"),), action="fail", extras={"reason": "fatal"}),
    Rule(
        when=(Pred("critic_reject", "truthy"), Pred("can_rewrite_cook", "truthy")),
        action="revise",
        extras={"reason": "critic"},
    ),
    Rule(when=(Pred("critic_reject", "truthy"),), action="fail", extras={"reason": "critic"}),
    Rule(when=(), action="pass", extras={"reason": "none"}),
)

_EMPIRICAL_DECISION_RULES = (
    Rule(
        when=(Pred("exposed", "truthy"), Pred("broken", "truthy")),
        action="fail",
        extras={"code": "empirical_likely_broken"},
    ),
    Rule(
        when=(Pred("exposed", "truthy"), Pred("neg_rpbis", "truthy")),
        action="fail",
        extras={"code": "empirical_non_discriminating"},
    ),
    Rule(
        when=(Pred("exposed", "truthy"), Pred("weak_rpbis", "truthy")),
        action="revise",
        extras={"code": "empirical_non_discriminating"},
    ),
    Rule(when=(), action="pass"),
)

_EMPIRICAL_P_RULES = (
    Rule(
        when=(Pred("exposed", "truthy"), Pred("too_easy", "truthy")),
        action="tag",
        extras={"code": "empirical_too_easy"},
    ),
    Rule(
        when=(
            Pred("exposed", "truthy"),
            Pred("too_hard", "truthy"),
            Pred("not_broken", "truthy"),
        ),
        action="tag",
        extras={"code": "empirical_too_hard"},
    ),
    Rule(when=(), action="skip"),
)

_REWRITE_STEP_RULES = (
    Rule(when=(Pred("first", "truthy"),), action="draft"),
    Rule(when=(Pred("can_rewrite", "truthy"),), action="rewrite"),
    Rule(when=(), action="regenerate"),
)

_PREFILTER_RULES = (
    Rule(when=(Pred("fatal", "truthy"),), action="fatal"),
    Rule(when=(Pred("too_similar", "truthy"),), action="too_similar"),
    Rule(when=(), action="keep"),
)

_HARD_BUNDLE_RULES = (
    Rule(when=(Pred("skip", "truthy"),), action="none"),
    Rule(when=(Pred("too_similar", "truthy"),), action="similar"),
    Rule(when=(Pred("has_verify", "truthy"),), action="verify"),
    Rule(when=(), action="heuristics"),
)


@dataclass(frozen=True)
class QualityScores:
    structure: float
    key: float
    distractors: float
    clarity: float
    overall: float


@dataclass(frozen=True)
class QualityVerdict:
    decision: Decision
    flaw_codes: tuple[str, ...]
    scores: QualityScores
    rewrite_brief: str
    fatal: bool
    stage: Stage = "cook"
    policy_version: str = QUALITY_VERSION
    reject_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualitySignals:
    """All evidence the decision core may see. Missing pieces are None/empty."""

    heuristic_flaws: Sequence[dict[str, str]] = ()
    verify_flaw: dict[str, str] | None = None
    critic: dict[str, Any] | None = None
    too_similar: bool = False
    max_similarity: float = 0.0
    rewrite_budget_remaining: int = 0
    verify_ran: bool = False
    stage: Stage = "cook"


@dataclass(frozen=True)
class SimilarityVerdict:
    """Near-dupe gate vs prior MCQs (owns ``MCQ_SIMILARITY_THRESHOLD``)."""

    too_similar: bool
    max_similarity: float
    threshold: float = MCQ_SIMILARITY_THRESHOLD
    policy: str = DEFAULT_QUALITY_POLICY
    policy_version: str = QUALITY_VERSION


def _empty_similarity(threshold: float, pol: str) -> SimilarityVerdict:
    return SimilarityVerdict(
        too_similar=False,
        max_similarity=0.0,
        threshold=threshold,
        policy=pol,
    )


def _max_sim_verdict(
    candidate_vec: list[float],
    prior_vecs: list[list[float]],
    threshold: float,
    pol: str,
    cosine_similarity: Any,
) -> SimilarityVerdict:
    max_sim = 0.0
    for prior_vec in prior_vecs:
        max_sim = max(max_sim, cosine_similarity(candidate_vec, prior_vec))
    return SimilarityVerdict(
        too_similar=max_sim >= threshold,
        max_similarity=max_sim,
        threshold=threshold,
        policy=pol,
    )


def judge_mcq_similarity(
    mcq: dict[str, Any],
    prior_mcqs: list[dict[str, Any]] | None,
    *,
    threshold: float = MCQ_SIMILARITY_THRESHOLD,
    prior_embeddings: list[list[float]] | None = None,
    policy: str | None = None,
) -> SimilarityVerdict:
    """Sole MCQ near-dupe seam. Embed plumbing stays in ``mcq_dedup``."""
    from app.services.mcq_dedup import (
        cosine_similarity,
        embed_signature_cached,
        mcq_signature,
        prior_mcq_embeddings,
    )

    pol = (policy or DEFAULT_QUALITY_POLICY).strip().lower() or DEFAULT_QUALITY_POLICY

    def with_priors() -> SimilarityVerdict:
        candidate_vec = embed_signature_cached(mcq_signature(mcq))
        prior_vecs = pick(
            prior_embeddings is not None,
            lambda: prior_embeddings,
            lambda: prior_mcq_embeddings(prior_mcqs or []),
        )
        return pick(
            not prior_vecs,
            lambda: _empty_similarity(threshold, pol),
            lambda: _max_sim_verdict(
                candidate_vec, prior_vecs, threshold, pol, cosine_similarity
            ),
        )

    return pick(
        not prior_mcqs and not prior_embeddings,
        lambda: _empty_similarity(threshold, pol),
        with_priors,
    )


def _codes_from_flaws(flaws: Sequence[dict[str, str]] | None) -> list[str]:
    codes = (str(f.get("code") or "").strip() for f in (flaws or []))
    return list(filter(None, codes))


def _code_from_critic_item(item: object) -> str:
    return pick(
        isinstance(item, dict),
        lambda: str(item.get("code") or ""),
        lambda: pick(isinstance(item, str), lambda: str(item), lambda: ""),
    )


def collect_flaw_codes(signals: QualitySignals) -> list[str]:
    """Stable ordered unique flaw codes from all cook-time signals."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add(code: str) -> None:
        def record() -> None:
            seen.add(code)
            ordered.append(code)

        pick(bool(code) and code not in seen, record, lambda: None)

    for code in _codes_from_flaws(signals.heuristic_flaws):
        add(code)
    add(choose(signals.too_similar, "too_similar_to_prior", ""))
    add(
        pick(
            bool(signals.verify_flaw),
            lambda: str(signals.verify_flaw.get("code") or "verify_failed"),
            lambda: "",
        )
    )
    critic = signals.critic or {}
    for code in critic.get("fatal_flaws") or []:
        add(str(code))
    for item in critic.get("flaws") or []:
        add(_code_from_critic_item(item))
    return ordered


def critique_passes(critique: dict[str, Any] | None) -> bool:
    """Product rule: pass flag, no fatal codes, at most one soft flaw."""
    return pick(
        not critique or not critique.get("pass"),
        lambda: False,
        lambda: (
            not any(code in FATAL_FLAW_CODES for code in (critique.get("fatal_flaws") or []))
            and int(critique.get("flaw_count") or 0) <= 1
        ),
    )


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_from_codes(
    codes: Sequence[str],
    *,
    verify_flaw: dict[str, str] | None,
    verify_ran: bool,
) -> QualityScores:
    """Informational 0–1 scores; pass-fail stays code-driven (no overall threshold)."""
    code_set = set(codes)
    structure = choose(bool(code_set & _STRUCTURE_FATAL), 0.0, 1.0)
    key = choose(
        bool(verify_flaw or (code_set & _KEY_BAD)),
        0.0,
        choose(verify_ran, 1.0, 0.7),
    )
    d_hits = len(code_set & SOFT_DISTRACTOR_CODES)
    distractors = _clamp01(1.0 - 0.25 * d_hits)
    c_hits = len(code_set & SOFT_CLARITY_CODES)
    clarity = _clamp01(1.0 - 0.25 * c_hits)
    overall = _clamp01(min(structure, key) * 0.5 + 0.25 * distractors + 0.25 * clarity)
    return QualityScores(
        structure=structure,
        key=key,
        distractors=distractors,
        clarity=clarity,
        overall=overall,
    )


def build_rewrite_brief(
    heuristic_flaws: Sequence[dict[str, str]] | None,
    critic: dict[str, Any] | None,
    verify_flaw: dict[str, str] | None = None,
) -> str:
    critic_hint = pick(
        bool(critic),
        lambda: str(critic.get("rewrite_hints") or "").strip(),
        lambda: "",
    )
    hints = list(filter(None, (critic_hint,)))
    hints.extend(
        f"{f.get('code') or '?'}: {f.get('message') or f.get('code') or '?'}"
        for f in (heuristic_flaws or [])
    )
    verify_hint = pick(
        bool(verify_flaw),
        lambda: (
            f"{verify_flaw.get('code') or 'verify_failed'}: "
            f"{verify_flaw.get('message') or verify_flaw.get('code') or 'verify_failed'}"
        ),
        lambda: "",
    )
    hints.extend(filter(None, (verify_hint,)))
    return " ".join(hints[:8]).strip()


def merge_critique_for_rewrite(
    heuristic_flaws: list[dict[str, str]],
    critique: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bundle flaws + hints for the rewrite prompt (cook path)."""
    extra = pick(
        bool(critique),
        lambda: list(critique.get("flaws") or [])
        + [
            {"code": code, "message": str(code).replace("_", " ")}
            for code in (critique.get("fatal_flaws") or [])
        ],
        lambda: [],
    )
    flaws = list(heuristic_flaws) + extra
    return {
        "flaws": flaws,
        "rewrite_hints": build_rewrite_brief(heuristic_flaws, critique),
        "fatal_flaws": [
            f.get("code")
            for f in filter(lambda row: row.get("code") in FATAL_FLAW_CODES, flaws)
        ],
    }


def decide_verdict(
    signals: QualitySignals,
    *,
    policy: str = DEFAULT_QUALITY_POLICY,
) -> QualityVerdict:
    """Compose signals into pass|fail|revise. Pure; no I/O.

    Policies:
    - code_driven_v1 (default): fatal codes and critic rule own the decision.
    Unknown policies degrade to code_driven_v1 (ADR 0004 safe fallback).
    """
    _ = policy  # reserved for future score_threshold / ensemble policies
    codes = collect_flaw_codes(signals)
    fatal_codes = list(filter(FATAL_FLAW_CODES.__contains__, codes))
    fatal = bool(fatal_codes)
    scores = score_from_codes(
        codes,
        verify_flaw=signals.verify_flaw,
        verify_ran=signals.verify_ran,
    )
    brief = build_rewrite_brief(
        signals.heuristic_flaws,
        signals.critic,
        signals.verify_flaw,
    )
    hit = first_match(
        _DECIDE_RULES,
        {
            "too_similar": signals.too_similar,
            "has_verify_flaw": signals.verify_flaw is not None,
            "fatal": fatal,
            "can_rewrite_cook": (
                signals.stage == "cook" and signals.rewrite_budget_remaining > 0
            ),
            "hard_no_rewrite": any(c in NO_REWRITE_CODES for c in fatal_codes),
            "critic_reject": signals.critic is not None
            and not critique_passes(signals.critic),
        },
    )
    reason = apply(
        hit.extras["reason"],
        {
            "too_similar": lambda: "too_similar_to_prior",
            "verify": lambda: str(signals.verify_flaw.get("code") or "verify_failed"),
            "fatal": lambda: pick(
                bool(fatal_codes), lambda: fatal_codes[0], lambda: "heuristic"
            ),
            "critic": lambda: "critic_rejected",
            "none": lambda: None,
        },
    )
    return QualityVerdict(
        decision=hit.action,  # type: ignore[arg-type]
        flaw_codes=tuple(codes),
        scores=scores,
        rewrite_brief=brief,
        fatal=fatal,
        stage=signals.stage,
        reject_reason=reason,
        details={
            "fatal_codes": fatal_codes,
            "policy": DEFAULT_QUALITY_POLICY,
        },
    )


def evaluate_empirical(
    *,
    p_correct: float,
    n_exposure: int,
    r_pbis: float | None = None,
    n_options: int = 4,
    min_exposure: int = EMPIRICAL_MIN_EXPOSURE,
    max_broken_rate: float = EMPIRICAL_MAX_BROKEN_RATE,
) -> QualityVerdict:
    """CTT post-serve flags. Auto-fail only for likely-broken near-zero first-try."""
    exposed = n_exposure >= min_exposure
    guess_floor = 1.0 / max(2, n_options)
    hit = first_match(
        _EMPIRICAL_DECISION_RULES,
        {
            "exposed": exposed,
            "broken": p_correct <= max_broken_rate,
            "neg_rpbis": r_pbis is not None and r_pbis < 0,
            "weak_rpbis": r_pbis is not None and r_pbis < EMPIRICAL_MIN_RPBIS,
        },
    )
    codes: list[str] = list(filter(None, (hit.extras.get("code"),)))
    p_hit = first_match(
        _EMPIRICAL_P_RULES,
        {
            "exposed": exposed,
            "too_easy": p_correct >= EMPIRICAL_EASY_P,
            "too_hard": p_correct <= max(EMPIRICAL_HARD_P, guess_floor - 0.02),
            "not_broken": "empirical_likely_broken" not in codes,
        },
    )
    codes.extend(filter(None, (p_hit.extras.get("code"),)))
    decision: Decision = hit.action  # type: ignore[assignment]
    reason = hit.extras.get("code")
    fatal = decision == "fail"
    disc = pick(
        r_pbis is not None,
        lambda: _clamp01((float(r_pbis) + 0.2) / 0.6),
        lambda: choose("empirical_likely_broken" in codes, 0.0, 1.0),
    )
    scores = QualityScores(
        structure=1.0,
        key=choose("empirical_likely_broken" in codes, 0.0, 0.7),
        distractors=disc,
        clarity=1.0,
        overall=_clamp01(0.5 * choose(fatal, 0.0, 0.7) + 0.5 * disc),
    )
    return QualityVerdict(
        decision=decision,
        flaw_codes=tuple(dict.fromkeys(codes)),
        scores=scores,
        rewrite_brief="",
        fatal=fatal,
        stage="empirical",
        reject_reason=reason,
        details={
            "p_correct": p_correct,
            "n_exposure": n_exposure,
            "r_pbis": r_pbis,
            "policy": DEFAULT_QUALITY_POLICY,
        },
    )


def should_run_critic(
    *,
    force_critic: bool,
    heuristic_flaws: Sequence[dict[str, str]],
    sample_roll: float,
    sample_rate: float,
) -> bool:
    """When to spend a critic call. sample_roll in [0, 1)."""
    return bool(force_critic) or bool(heuristic_flaws) or sample_roll < sample_rate


def evaluate_pre_critic_reject(signals: QualitySignals) -> QualityVerdict | None:
    """Skip critic spend when the hard cook gate already failed."""
    verdict = decide_verdict(signals)
    hard = (
        signals.too_similar
        or signals.verify_flaw is not None
        or has_fatal_heuristic_flaws(list(signals.heuristic_flaws))
    )
    return pick(verdict.decision == "fail" and hard, lambda: verdict, lambda: None)


def _similar_rewrite_bundle(signals: QualitySignals) -> dict[str, Any]:
    return {
        "flaws": [
            {
                "code": "too_similar_to_prior",
                "message": (
                    f"Embedding similarity {signals.max_similarity:.2f} "
                    "to a prior question"
                ),
            }
        ],
        "rewrite_hints": (
            "Test a different fact and use a clearly different stem "
            "from all prior questions."
        ),
        "fatal_flaws": ["too_similar_to_prior"],
    }


def hard_fail_rewrite_bundle(signals: QualitySignals) -> dict[str, Any] | None:
    """Rewrite hints when the serial cook skips critic after a hard fail."""
    hit = first_match(
        _HARD_BUNDLE_RULES,
        {
            "skip": evaluate_pre_critic_reject(signals) is None,
            "too_similar": signals.too_similar,
            "has_verify": signals.verify_flaw is not None,
        },
    )
    return apply(
        hit.action,
        {
            "none": lambda: None,
            "similar": lambda: _similar_rewrite_bundle(signals),
            "verify": lambda: merge_critique_for_rewrite(
                [dict(signals.verify_flaw)], None
            ),
            "heuristics": lambda: merge_critique_for_rewrite(
                list(signals.heuristic_flaws), None
            ),
        },
    )


def evaluate_clone_template(draft: dict[str, Any]) -> QualityVerdict:
    """Reuse is not a bypass: re-attest a cloned MCQ before persist."""
    h_flaws = run_heuristic_checks(draft)
    return decide_verdict(
        QualitySignals(
            heuristic_flaws=h_flaws,
            too_similar=False,
            rewrite_budget_remaining=0,
        )
    )


RewriteStep = Literal["draft", "rewrite", "regenerate"]


def plan_rewrite_step(
    *,
    attempt: int,
    has_draft: bool,
    has_rewrite_brief: bool,
) -> RewriteStep:
    """First try drafts; later tries rewrite when a brief exists, else regenerate."""
    hit = first_match(
        _REWRITE_STEP_RULES,
        {
            "first": int(attempt) <= 0,
            "can_rewrite": has_draft and has_rewrite_brief,
        },
    )
    return hit.action  # type: ignore[return-value]


NEWSPAPER_CONTENT_TYPE = "newspaper_upsc"


def resolve_cook_content_type(
    *,
    newspaper: bool,
    coverage_type: str | None,
) -> str | None:
    """Newspaper cooks always use the exam style; others keep triage content_type."""
    return choose(newspaper, NEWSPAPER_CONTENT_TYPE, coverage_type)


def should_inject_mcq_content_style(
    content_type: str | None,
    *,
    content_aware: bool,
) -> bool:
    """Newspaper always injects; other types follow the content-aware flag."""
    ct = (content_type or "").strip().lower()
    return ct == NEWSPAPER_CONTENT_TYPE or bool(content_aware)


def should_fast_path_accept(signals: QualitySignals) -> bool:
    """Serial cook: skip critic when structure, key, and similarity are clean."""
    return (
        not signals.heuristic_flaws
        and signals.verify_flaw is None
        and not signals.too_similar
    )


_ANSWER_KEY_VERIFY_RULES = (
    Rule(when=(Pred("enabled", "falsey"),), action="skip"),
    Rule(when=(Pred("fatal", "truthy"),), action="skip"),
    Rule(when=(Pred("too_similar", "truthy"),), action="skip"),
    Rule(when=(), action="run"),
)

_SIMILARITY_GATE_RULES = (
    Rule(when=(Pred("fatal", "truthy"),), action="skip"),
    Rule(when=(Pred("verify_failed", "truthy"),), action="skip"),
    Rule(when=(), action="run"),
)


def should_run_answer_key_verify(
    *,
    verify_enabled: bool,
    has_fatal_heuristics: bool,
    too_similar: bool = False,
) -> bool:
    """Skip the verifier when structure already failed or the draft is a near-dupe."""
    return (
        first_match(
            _ANSWER_KEY_VERIFY_RULES,
            {
                "enabled": bool(verify_enabled),
                "fatal": has_fatal_heuristics,
                "too_similar": too_similar,
            },
        ).action
        == "run"
    )


def should_run_similarity_gate(
    *,
    has_fatal_heuristics: bool,
    verify_flaw: dict[str, Any] | None = None,
) -> bool:
    """Skip embed when heuristics or the answer-key check already failed."""
    return (
        first_match(
            _SIMILARITY_GATE_RULES,
            {
                "fatal": has_fatal_heuristics,
                "verify_failed": verify_flaw is not None,
            },
        ).action
        == "run"
    )


def should_positional_best_of_n(*, selected_count: int) -> bool:
    """When aspect-key grouping matched nothing, pair drafts by position."""
    return int(selected_count) <= 0


ParallelPrefilter = Literal["fatal", "too_similar", "keep"]


def evaluate_parallel_gate_prefilter(
    *,
    heuristic_flaws: Sequence[dict[str, str]],
    too_similar: bool,
) -> ParallelPrefilter:
    """Drop fatal / near-dupe drafts before spending parallel critic slots."""
    hit = first_match(
        _PREFILTER_RULES,
        {
            "fatal": has_fatal_heuristic_flaws(list(heuristic_flaws)),
            "too_similar": too_similar,
        },
    )
    return hit.action  # type: ignore[return-value]


# Batch cook: cap + serial Q1 critic + parallel rest. Env knobs stay at cook plumbing.
BATCH_MCQ_CAP = 5


@dataclass(frozen=True)
class CookGateSchedule:
    target_count: int
    batch_cap: int
    split_first_draft: bool
    first_force_critic: bool
    rest_force_critic: bool
    rest_concurrency: int
    policy_version: str = QUALITY_VERSION


def plan_cook_gate_schedule(
    *,
    target_count: int,
    pipeline_split: bool,
    generation_concurrency: int,
    batch_cap: int = BATCH_MCQ_CAP,
) -> CookGateSchedule:
    """Serial first-draft critic, then parallel rest, capped per LLM batch."""
    cap = max(1, int(batch_cap))
    n = max(0, min(int(target_count), cap))
    workers = max(1, int(generation_concurrency))
    return CookGateSchedule(
        target_count=n,
        batch_cap=cap,
        split_first_draft=bool(pipeline_split) and n > 1,
        first_force_critic=True,
        rest_force_critic=False,
        rest_concurrency=workers,
    )


@dataclass(frozen=True)
class CookGateKnobs:
    generation_concurrency: int
    pipeline_draft_split: bool
    critic_sample_rate: float
    policy_version: str = QUALITY_VERSION


def plan_cook_gate_knobs(
    *,
    generation_concurrency: int | None = None,
    pipeline_split: bool | None = None,
    critic_sample_rate: float | None = None,
) -> CookGateKnobs:
    """Resolve cook-gate concurrency, pipeline split, and critic sample rate."""
    return CookGateKnobs(
        pick(
            generation_concurrency is None,
            lambda: GENERATION_CONCURRENCY,
            lambda: max(1, int(generation_concurrency)),
        ),
        pick(
            pipeline_split is None,
            lambda: PIPELINE_DRAFT_SPLIT,
            lambda: bool(pipeline_split),
        ),
        pick(
            critic_sample_rate is None,
            lambda: CRITIC_SAMPLE_RATE,
            lambda: float(critic_sample_rate),
        ),
    )


def plan_best_of_n_candidates(requested: int | None = None) -> int:
    """How many draft candidates to write per aspect before structural pick."""
    n = pick(
        requested is None,
        lambda: DEFAULT_CANDIDATES_PER_ASPECT,
        lambda: int(requested),
    )
    return max(1, n)


def plan_prior_mcq_prompt_items(requested: int | None = None) -> int:
    """How many already-cooked stems to paste so the next draft does not repeat them."""
    n = pick(
        requested is None,
        lambda: PRIOR_MCQ_PROMPT_ITEMS,
        lambda: int(requested),
    )
    return max(1, n)


def draft_structural_score(draft: dict[str, Any]) -> tuple[int, int, float]:
    """Lower is better. Free structural signal for best-of-N draft pick.

    Fewest fatal flaws, then fewest total flaws, then smallest correct-option
    length gap vs other options (guards longest-answer giveaway).
    """
    flaws = run_heuristic_checks(draft)
    fatal = sum(
        1 for f in filter(lambda row: row.get("code") in FATAL_FLAW_CODES, flaws)
    )
    options = [str(o) for o in (draft.get("options") or [])]
    gap = 0.0
    try:
        ci = int(draft.get("correct_index"))
        others = [
            len(o)
            for _i, o in filter(lambda pair: pair[0] != ci, enumerate(options))
        ]
        gap = pick(
            bool(others),
            lambda: abs(len(options[ci]) - sum(others) / len(others)),
            lambda: 0.0,
        )
    except (TypeError, ValueError, IndexError):
        gap = 0.0
    return (fatal, len(flaws), gap)


def pick_best_draft(candidates: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick structurally best draft among LLM alternatives (no LLM)."""
    return pick(
        not candidates,
        lambda: None,
        lambda: min(candidates, key=draft_structural_score),
    )


# Re-export leaf helpers so cook/admin can import one engine module.
__all__ = [
    "QUALITY_VERSION",
    "DEFAULT_QUALITY_POLICY",
    "MCQ_SIMILARITY_THRESHOLD",
    "DEFAULT_CRITIC_SAMPLE_RATE",
    "FATAL_FLAW_CODES",
    "NO_REWRITE_CODES",
    "EMPIRICAL_MIN_EXPOSURE",
    "EMPIRICAL_MAX_BROKEN_RATE",
    "QualityScores",
    "QualityVerdict",
    "QualitySignals",
    "SimilarityVerdict",
    "collect_flaw_codes",
    "critique_passes",
    "score_from_codes",
    "build_rewrite_brief",
    "merge_critique_for_rewrite",
    "judge_mcq_similarity",
    "decide_verdict",
    "evaluate_empirical",
    "should_run_critic",
    "evaluate_pre_critic_reject",
    "hard_fail_rewrite_bundle",
    "evaluate_clone_template",
    "MAX_GENERATION_ATTEMPTS",
    "plan_rewrite_step",
    "resolve_cook_content_type",
    "should_inject_mcq_content_style",
    "should_fast_path_accept",
    "should_run_answer_key_verify",
    "should_run_similarity_gate",
    "should_positional_best_of_n",
    "evaluate_parallel_gate_prefilter",
    "BATCH_MCQ_CAP",
    "GENERATION_CONCURRENCY",
    "PIPELINE_DRAFT_SPLIT",
    "CRITIC_SAMPLE_RATE",
    "CookGateSchedule",
    "CookGateKnobs",
    "plan_cook_gate_schedule",
    "plan_cook_gate_knobs",
    "plan_best_of_n_candidates",
    "plan_prior_mcq_prompt_items",
    "draft_structural_score",
    "pick_best_draft",
    "run_heuristic_checks",
    "has_fatal_heuristic_flaws",
    "FATAL_FLAW_CODES",
]
