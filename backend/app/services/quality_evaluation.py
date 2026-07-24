"""Quality Evaluation Engine — pure decision core (policy seam).

Design: docs/QUALITY_EVALUATION_ENGINE.md
Version: qb.quality.v1

Deterministic composition of heuristic / verify / critic / similarity / CTT
signals into pass|fail|revise. LLM I/O stays in mcq_quality; this module owns
the boolean and the scores. Callers depend on this seam (ADR 0004), not on
ad-hoc if-ladders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from app.services.mcq_heuristics import (
    FATAL_FLAW_CODES,
    has_fatal_heuristic_flaws,
    run_heuristic_checks,
)

QUALITY_VERSION = "qb.quality.v1"
DEFAULT_QUALITY_POLICY = "code_driven_v1"

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


# Re-export leaf helpers so cook/admin can import one engine module.
__all__ = [
    "QUALITY_VERSION",
    "DEFAULT_QUALITY_POLICY",
    "FATAL_FLAW_CODES",
    "NO_REWRITE_CODES",
    "EMPIRICAL_MIN_EXPOSURE",
    "EMPIRICAL_MAX_BROKEN_RATE",
    "QualityScores",
    "QualityVerdict",
    "QualitySignals",
    "collect_flaw_codes",
    "critique_passes",
    "score_from_codes",
    "build_rewrite_brief",
    "merge_critique_for_rewrite",
    "decide_verdict",
    "evaluate_empirical",
    "should_run_critic",
    "run_heuristic_checks",
    "has_fatal_heuristic_flaws",
]
