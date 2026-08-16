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
    if not prior_mcqs and not prior_embeddings:
        return SimilarityVerdict(
            too_similar=False,
            max_similarity=0.0,
            threshold=threshold,
            policy=pol,
        )

    candidate_vec = embed_signature_cached(mcq_signature(mcq))
    prior_vecs = (
        prior_embeddings
        if prior_embeddings is not None
        else prior_mcq_embeddings(prior_mcqs or [])
    )
    if not prior_vecs:
        return SimilarityVerdict(
            too_similar=False,
            max_similarity=0.0,
            threshold=threshold,
            policy=pol,
        )

    max_sim = 0.0
    for prior_vec in prior_vecs:
        max_sim = max(max_sim, cosine_similarity(candidate_vec, prior_vec))
    return SimilarityVerdict(
        too_similar=max_sim >= threshold,
        max_similarity=max_sim,
        threshold=threshold,
        policy=pol,
    )


def _codes_from_flaws(flaws: Sequence[dict[str, str]] | None) -> list[str]:
    out: list[str] = []
    for f in flaws or []:
        code = str(f.get("code") or "").strip()
        if code:
            out.append(code)
    return out


def collect_flaw_codes(signals: QualitySignals) -> list[str]:
    """Stable ordered unique flaw codes from all cook-time signals."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add(code: str) -> None:
        if code and code not in seen:
            seen.add(code)
            ordered.append(code)

    for code in _codes_from_flaws(signals.heuristic_flaws):
        add(code)
    if signals.too_similar:
        add("too_similar_to_prior")
    if signals.verify_flaw:
        add(str(signals.verify_flaw.get("code") or "verify_failed"))
    if signals.critic:
        for code in signals.critic.get("fatal_flaws") or []:
            add(str(code))
        for f in signals.critic.get("flaws") or []:
            if isinstance(f, dict):
                add(str(f.get("code") or ""))
            elif isinstance(f, str):
                add(f)
    return ordered


def critique_passes(critique: dict[str, Any] | None) -> bool:
    """Product rule: pass flag, no fatal codes, at most one soft flaw."""
    if not critique or not critique.get("pass"):
        return False
    fatal = critique.get("fatal_flaws") or []
    if any(code in FATAL_FLAW_CODES for code in fatal):
        return False
    flaw_count = int(critique.get("flaw_count") or 0)
    return flaw_count <= 1


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
    structure = 0.0 if (code_set & FATAL_FLAW_CODES & {
        "invalid_structure",
        "none_or_all_of_above",
        "negative_wording",
        "longest_option_correct",
        "unfocused_stem",
        "not_self_contained",
        "meta_page_reference",
        "too_similar_to_prior",
    }) else 1.0

    key_bad = {"wrong_answer_key", "more_than_one_correct", "not_grounded"}
    if verify_flaw or (code_set & key_bad):
        key = 0.0
    elif verify_ran:
        key = 1.0
    else:
        key = 0.7

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
    hints: list[str] = []
    if critic:
        hint = str(critic.get("rewrite_hints") or "").strip()
        if hint:
            hints.append(hint)
    for f in heuristic_flaws or []:
        code = f.get("code") or "?"
        msg = f.get("message") or code
        hints.append(f"{code}: {msg}")
    if verify_flaw:
        code = verify_flaw.get("code") or "verify_failed"
        msg = verify_flaw.get("message") or code
        hints.append(f"{code}: {msg}")
    return " ".join(hints[:8]).strip()


def merge_critique_for_rewrite(
    heuristic_flaws: list[dict[str, str]],
    critique: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bundle flaws + hints for the rewrite prompt (cook path)."""
    flaws = list(heuristic_flaws)
    if critique:
        flaws.extend(critique.get("flaws") or [])
        for code in critique.get("fatal_flaws") or []:
            flaws.append({"code": code, "message": str(code).replace("_", " ")})
    return {
        "flaws": flaws,
        "rewrite_hints": build_rewrite_brief(heuristic_flaws, critique),
        "fatal_flaws": [f.get("code") for f in flaws if f.get("code") in FATAL_FLAW_CODES],
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
    fatal_codes = [c for c in codes if c in FATAL_FLAW_CODES]
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

    def _verdict(
        decision: Decision,
        *,
        reason: str | None = None,
    ) -> QualityVerdict:
        return QualityVerdict(
            decision=decision,
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

    # 1. Hard similarity / verify / fatal heuristics
    if signals.too_similar:
        return _verdict("fail", reason="too_similar_to_prior")

    if signals.verify_flaw is not None:
        reason = str(signals.verify_flaw.get("code") or "verify_failed")
        return _verdict("fail", reason=reason)

    if fatal:
        hard = any(c in NO_REWRITE_CODES for c in fatal_codes)
        if (
            signals.stage == "cook"
            and signals.rewrite_budget_remaining > 0
            and not hard
        ):
            return _verdict("revise", reason=fatal_codes[0])
        return _verdict("fail", reason=fatal_codes[0] if fatal_codes else "heuristic")

    # 2. Critic soft path
    if signals.critic is not None and not critique_passes(signals.critic):
        if signals.rewrite_budget_remaining > 0 and signals.stage == "cook":
            return _verdict("revise", reason="critic_rejected")
        return _verdict("fail", reason="critic_rejected")

    return _verdict("pass")


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
    codes: list[str] = []
    decision: Decision = "pass"
    reason: str | None = None

    if n_exposure >= min_exposure and p_correct <= max_broken_rate:
        codes.append("empirical_likely_broken")
        decision = "fail"
        reason = "empirical_likely_broken"
    elif r_pbis is not None and n_exposure >= min_exposure and r_pbis < 0:
        codes.append("empirical_non_discriminating")
        decision = "fail"
        reason = "empirical_non_discriminating"
    elif (
        r_pbis is not None
        and n_exposure >= min_exposure
        and r_pbis < EMPIRICAL_MIN_RPBIS
    ):
        codes.append("empirical_non_discriminating")
        decision = "revise"
        reason = "empirical_non_discriminating"

    guess_floor = 1.0 / max(2, n_options)
    if n_exposure >= min_exposure:
        if p_correct >= EMPIRICAL_EASY_P:
            codes.append("empirical_too_easy")
        elif p_correct <= max(EMPIRICAL_HARD_P, guess_floor - 0.02):
            if "empirical_likely_broken" not in codes:
                codes.append("empirical_too_hard")

    fatal = decision == "fail"
    # Empirical scores: key/structure unknown; encode discrimination in overall.
    disc = 1.0
    if r_pbis is not None:
        disc = _clamp01((r_pbis + 0.2) / 0.6)
    elif "empirical_likely_broken" in codes:
        disc = 0.0
    scores = QualityScores(
        structure=1.0,
        key=0.0 if "empirical_likely_broken" in codes else 0.7,
        distractors=disc,
        clarity=1.0,
        overall=_clamp01(0.5 * (0.0 if fatal else 0.7) + 0.5 * disc),
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
    if force_critic:
        return True
    if heuristic_flaws:
        return True
    return sample_roll < sample_rate


def evaluate_pre_critic_reject(signals: QualitySignals) -> QualityVerdict | None:
    """Skip critic spend when the hard cook gate already failed."""
    verdict = decide_verdict(signals)
    hard = (
        signals.too_similar
        or signals.verify_flaw is not None
        or has_fatal_heuristic_flaws(list(signals.heuristic_flaws))
    )
    if verdict.decision == "fail" and hard:
        return verdict
    return None


def hard_fail_rewrite_bundle(signals: QualitySignals) -> dict[str, Any] | None:
    """Rewrite hints when the serial cook skips critic after a hard fail."""
    if evaluate_pre_critic_reject(signals) is None:
        return None
    if signals.too_similar:
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
    if signals.verify_flaw is not None:
        return merge_critique_for_rewrite([dict(signals.verify_flaw)], None)
    return merge_critique_for_rewrite(list(signals.heuristic_flaws), None)


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
    if int(attempt) <= 0:
        return "draft"
    if has_draft and has_rewrite_brief:
        return "rewrite"
    return "regenerate"


NEWSPAPER_CONTENT_TYPE = "newspaper_upsc"


def resolve_cook_content_type(
    *,
    newspaper: bool,
    coverage_type: str | None,
) -> str | None:
    """Newspaper cooks always use the exam style; others keep triage content_type."""
    if newspaper:
        return NEWSPAPER_CONTENT_TYPE
    return coverage_type


def should_inject_mcq_content_style(
    content_type: str | None,
    *,
    content_aware: bool,
) -> bool:
    """Newspaper always injects; other types follow the content-aware flag."""
    ct = (content_type or "").strip().lower()
    if ct == NEWSPAPER_CONTENT_TYPE:
        return True
    return bool(content_aware)


def should_fast_path_accept(signals: QualitySignals) -> bool:
    """Serial cook: skip critic when structure, key, and similarity are clean."""
    return (
        not signals.heuristic_flaws
        and signals.verify_flaw is None
        and not signals.too_similar
    )


def should_run_answer_key_verify(
    *,
    verify_enabled: bool,
    has_fatal_heuristics: bool,
    too_similar: bool = False,
) -> bool:
    """Skip the verifier when structure already failed or the draft is a near-dupe."""
    return bool(verify_enabled) and not has_fatal_heuristics and not too_similar


def should_run_similarity_gate(
    *,
    has_fatal_heuristics: bool,
    verify_flaw: dict[str, Any] | None = None,
) -> bool:
    """Skip embed when heuristics or the answer-key check already failed."""
    return not has_fatal_heuristics and verify_flaw is None


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
    if has_fatal_heuristic_flaws(list(heuristic_flaws)):
        return "fatal"
    if too_similar:
        return "too_similar"
    return "keep"


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
        GENERATION_CONCURRENCY
        if generation_concurrency is None
        else max(1, int(generation_concurrency)),
        PIPELINE_DRAFT_SPLIT if pipeline_split is None else bool(pipeline_split),
        CRITIC_SAMPLE_RATE if critic_sample_rate is None else float(critic_sample_rate),
    )


def plan_best_of_n_candidates(requested: int | None = None) -> int:
    """How many draft candidates to write per aspect before structural pick."""
    n = DEFAULT_CANDIDATES_PER_ASPECT if requested is None else int(requested)
    return max(1, n)


def plan_prior_mcq_prompt_items(requested: int | None = None) -> int:
    """How many already-cooked stems to paste so the next draft does not repeat them."""
    n = PRIOR_MCQ_PROMPT_ITEMS if requested is None else int(requested)
    return max(1, n)


def draft_structural_score(draft: dict[str, Any]) -> tuple[int, int, float]:
    """Lower is better. Free structural signal for best-of-N draft pick.

    Fewest fatal flaws, then fewest total flaws, then smallest correct-option
    length gap vs other options (guards longest-answer giveaway).
    """
    flaws = run_heuristic_checks(draft)
    fatal = sum(1 for f in flaws if f.get("code") in FATAL_FLAW_CODES)
    options = [str(o) for o in (draft.get("options") or [])]
    gap = 0.0
    try:
        ci = int(draft.get("correct_index"))
        others = [len(o) for i, o in enumerate(options) if i != ci]
        if others:
            gap = abs(len(options[ci]) - sum(others) / len(others))
    except (TypeError, ValueError, IndexError):
        gap = 0.0
    return (fatal, len(flaws), gap)


def pick_best_draft(candidates: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick structurally best draft among LLM alternatives (no LLM)."""
    if not candidates:
        return None
    return min(candidates, key=draft_structural_score)


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
]
