"""Quality Evaluation Engine — decide_verdict / empirical / scores."""

from __future__ import annotations

from app.services.quality_evaluation import (
    FATAL_FLAW_CODES,
    QUALITY_VERSION,
    QualitySignals,
    critique_passes,
    decide_verdict,
    evaluate_empirical,
    merge_critique_for_rewrite,
    should_run_critic,
)


def test_quality_version_locked() -> None:
    assert QUALITY_VERSION == "qb.quality.v1"


def test_haladyna_fatal_codes_present() -> None:
    required = {
        "ambiguous_unclear",
        "more_than_one_correct",
        "implausible_distractors",
        "none_or_all_of_above",
        "wrong_answer_key",
        "not_grounded",
        "recognition_only",
        "invalid_structure",
    }
    assert required <= FATAL_FLAW_CODES


def test_clean_signals_pass() -> None:
    v = decide_verdict(QualitySignals(verify_ran=True))
    assert v.decision == "pass"
    assert v.fatal is False
    assert v.scores.structure == 1.0
    assert v.scores.key == 1.0


def test_judge_mcq_similarity_uses_threshold() -> None:
    from unittest.mock import patch

    from app.services.quality_evaluation import (
        MCQ_SIMILARITY_THRESHOLD,
        judge_mcq_similarity,
    )

    mcq = {"question": "What is X?", "options": ["a", "b"], "correct_index": 0}
    prior = [{"question": "What is X?", "correct_answer": "a"}]

    with (
        patch(
            "app.services.mcq_dedup.embed_signature_cached",
            return_value=[1.0, 0.0],
        ),
        patch(
            "app.services.mcq_dedup.prior_mcq_embeddings",
            return_value=[[1.0, 0.0]],
        ),
    ):
        v = judge_mcq_similarity(mcq, prior, threshold=0.92)
    assert v.too_similar is True
    assert v.max_similarity >= 0.99
    assert v.threshold == 0.92
    assert MCQ_SIMILARITY_THRESHOLD == 0.92
    assert v.policy_version == QUALITY_VERSION


def test_fatal_heuristic_fails_without_rewrite() -> None:
    v = decide_verdict(
        QualitySignals(
            heuristic_flaws=[{"code": "none_or_all_of_above", "message": "bad"}],
            rewrite_budget_remaining=0,
        )
    )
    assert v.decision == "fail"
    assert v.fatal is True
    assert "none_or_all_of_above" in v.flaw_codes
    assert v.reject_reason == "none_or_all_of_above"


def test_soft_fatal_with_rewrite_budget_revises() -> None:
    # Negative wording is fatal but rewritable (not in NO_REWRITE_CODES).
    v = decide_verdict(
        QualitySignals(
            heuristic_flaws=[{"code": "negative_wording", "message": "hidden not"}],
            rewrite_budget_remaining=1,
        )
    )
    assert v.decision == "revise"
    assert v.fatal is True


def test_wrong_answer_key_never_revises() -> None:
    v = decide_verdict(
        QualitySignals(
            verify_flaw={"code": "wrong_answer_key", "message": "mismatch"},
            verify_ran=True,
            rewrite_budget_remaining=3,
        )
    )
    assert v.decision == "fail"
    assert v.reject_reason == "wrong_answer_key"
    assert v.scores.key == 0.0


def test_too_similar_fails() -> None:
    v = decide_verdict(QualitySignals(too_similar=True, max_similarity=0.95))
    assert v.decision == "fail"
    assert "too_similar_to_prior" in v.flaw_codes


def test_critic_pass_true_but_recognition_only_fatal_fails() -> None:
    critique = {"pass": True, "flaw_count": 0, "fatal_flaws": ["recognition_only"]}
    assert critique_passes(critique) is False
    v = decide_verdict(
        QualitySignals(critic=critique, rewrite_budget_remaining=0, verify_ran=True)
    )
    assert v.decision == "fail"
    assert "recognition_only" in v.flaw_codes


def test_critic_reject_revises_then_fails() -> None:
    # Soft reject: pass=False with non-fatal codes only (flaw_count path).
    critique = {
        "pass": False,
        "flaw_count": 3,
        "fatal_flaws": [],
        "flaws": [{"code": "grammatical_cues", "message": "cue"}],
        "rewrite_hints": "Tighten the stem.",
    }
    revise = decide_verdict(
        QualitySignals(critic=critique, rewrite_budget_remaining=1, verify_ran=True)
    )
    assert revise.decision == "revise"
    assert "Tighten the stem" in revise.rewrite_brief

    fail = decide_verdict(
        QualitySignals(critic=critique, rewrite_budget_remaining=0, verify_ran=True)
    )
    assert fail.decision == "fail"
    assert fail.reject_reason == "critic_rejected"


def test_critique_passes_soft_tolerance() -> None:
    assert critique_passes({"pass": True, "flaw_count": 1, "fatal_flaws": []}) is True
    assert critique_passes({"pass": True, "flaw_count": 2, "fatal_flaws": []}) is False


def test_merge_rewrite_brief_includes_heuristic_and_critic() -> None:
    bundle = merge_critique_for_rewrite(
        [{"code": "unfocused_stem", "message": "no question mark"}],
        {"rewrite_hints": "Ask one clear question.", "fatal_flaws": [], "flaws": []},
    )
    assert "unfocused_stem" in bundle["rewrite_hints"]
    assert "Ask one clear question" in bundle["rewrite_hints"]


def test_should_run_critic_force_and_sample() -> None:
    assert should_run_critic(
        force_critic=True, heuristic_flaws=[], sample_roll=0.99, sample_rate=0.0
    )
    assert should_run_critic(
        force_critic=False,
        heuristic_flaws=[{"code": "grammatical_cues", "message": "x"}],
        sample_roll=0.99,
        sample_rate=0.0,
    )
    assert should_run_critic(
        force_critic=False, heuristic_flaws=[], sample_roll=0.1, sample_rate=0.5
    )
    assert not should_run_critic(
        force_critic=False, heuristic_flaws=[], sample_roll=0.9, sample_rate=0.5
    )


def test_empirical_broken_fails() -> None:
    v = evaluate_empirical(p_correct=0.05, n_exposure=12)
    assert v.decision == "fail"
    assert "empirical_likely_broken" in v.flaw_codes
    assert v.stage == "empirical"


def test_empirical_cold_start_no_flag() -> None:
    v = evaluate_empirical(p_correct=0.0, n_exposure=3)
    assert v.decision == "pass"
    assert v.flaw_codes == ()


def test_empirical_negative_rpbis_fails() -> None:
    v = evaluate_empirical(p_correct=0.5, n_exposure=20, r_pbis=-0.1)
    assert v.decision == "fail"
    assert "empirical_non_discriminating" in v.flaw_codes


def test_empirical_low_rpbis_revises_not_auto_retire() -> None:
    v = evaluate_empirical(p_correct=0.55, n_exposure=20, r_pbis=0.12)
    assert v.decision == "revise"
    assert "empirical_non_discriminating" in v.flaw_codes


def test_unknown_policy_degrades_safely() -> None:
    v = decide_verdict(QualitySignals(verify_ran=True), policy="does_not_exist_yet")
    assert v.decision == "pass"
