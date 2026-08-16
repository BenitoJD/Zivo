"""Open Response Measurement Engine - mains + interview rubrics."""

from __future__ import annotations

from app.services.open_response import (
    OPEN_RESPONSE_VERSION,
    build_interview_report,
    clamp_interview_score,
    mains_band,
    shape_interview_scores,
    shape_mains_result,
)


def test_mains_shape_clamps_axes() -> None:
    v = shape_mains_result(
        {
            "marks": 99,
            "axes": {"directive": 9, "structure": -1},
            "scheme_hits": [{"index": 1, "hit": True}],
            "keep_doing": ["a", "b", "c", "d"],
            "improve": [],
            "examiner_note": "ok",
        },
        {"marks_max": 10, "model_points": [{"point": "P1", "marks": 2}]},
        "coaching",
    )
    assert v.kind == "mains"
    assert v.policy_version == OPEN_RESPONSE_VERSION
    assert v.result["marks"] == 10
    axes = {a["key"]: a["score"] for a in v.result["axes"]}
    assert axes["directive"] == 5
    assert axes["structure"] == 0
    assert v.result["scheme_hits"][0]["hit"] is True
    assert len(v.result["keep_doing"]) == 3


def test_mains_band_strictness() -> None:
    assert mains_band(7, 10, "exam") == "Good"
    assert mains_band(7, 10, "gentle") == "Excellent"


def test_interview_score_clamp() -> None:
    assert clamp_interview_score(9) == 4
    assert clamp_interview_score(0) == 1
    assert clamp_interview_score("x") == 2


def test_interview_report_typed_average() -> None:
    cfg = [{"name": "Technical", "kind": "typed"}]
    transcript = [
        {"round_name": "Technical", "scores": {"problem_framing": 4, "depth": 4, "tradeoffs": 4, "communication": 4}},
    ]
    v = build_interview_report(cfg, transcript)
    assert v.result["overall"] == 100
    assert "Technical" in v.result["strengths"]


def test_shape_interview_scores() -> None:
    v = shape_interview_scores({"problem_framing": 3, "depth": 99})
    assert v.result["scores"]["problem_framing"] == 3
    assert v.result["scores"]["depth"] == 4
    assert v.result["scores"]["tradeoffs"] == 2


def test_heuristic_coding_teach_gap_fail_shape() -> None:
    from app.services.open_response import heuristic_coding_teach_gap

    v = heuristic_coding_teach_gap(
        all_passed=False,
        passed=1,
        total=3,
        tags=["arrays", "two-pointers"],
        concept="Two sum",
        first_fail={"stdin": "1\n", "expected": "2\n", "stdout": "0\n", "stderr": ""},
    )
    assert v.kind == "coding_teach"
    assert v.policy_version == OPEN_RESPONSE_VERSION
    assert v.result["mentor_summary"]
    assert v.result["lesson"]["try_this"]
    assert "arrays" in v.result["weak_concepts"] or "two-pointers" in v.result["weak_concepts"]


def test_heuristic_coding_teach_gap_pass_still_teaches() -> None:
    from app.services.open_response import heuristic_coding_teach_gap

    v = heuristic_coding_teach_gap(
        all_passed=True,
        passed=3,
        total=3,
        tags=["dp"],
        concept="Knapsack",
        first_fail=None,
    )
    assert v.result["lesson"]["title"]
    assert v.result["weak_concepts"]


def test_interview_coding_turn_pass_and_fail() -> None:
    from app.services.open_response import evaluate_interview_coding_turn

    passed = evaluate_interview_coding_turn(
        source="print(1)",
        language_id=71,
        passed=3,
        total=3,
    )
    assert passed.kind == "interview_coding"
    assert passed.result["correct"] is True
    failed = evaluate_interview_coding_turn(
        source="print(1)",
        language_id=71,
        passed=1,
        total=3,
        first_fail={"stderr": "", "stdout": "1", "expected": "2"},
    )
    assert failed.result["correct"] is False
    assert "1/3" in failed.result["feedback"]
    broken = evaluate_interview_coding_turn(
        source="",
        language_id=71,
        passed=0,
        total=0,
        error="timeout",
    )
    assert "timeout" in broken.result["feedback"]


def test_degraded_interview_scores_are_mid_scale() -> None:
    from app.services.open_response import INTERVIEW_DIMENSIONS, degraded_interview_scores

    v = degraded_interview_scores()
    assert v.kind == "interview_typed"
    assert v.result["scores"] == {d: 2 for d in INTERVIEW_DIMENSIONS}


def test_mains_attempt_and_coding_verify_gates() -> None:
    from app.services.open_response import (
        evaluate_coding_reference_verify,
        plan_coding_test_visibility,
        plan_mains_attempt,
    )

    ten = plan_mains_attempt(strictness="nope", marks_max=10)
    assert ten.strictness == "coaching"
    assert ten.marks_max == 10
    assert ten.word_target == 150
    fifteen = plan_mains_attempt(strictness="exam", marks_max=15)
    assert fifteen.marks_max == 15
    assert fifteen.word_target == 250
    vis = plan_coding_test_visibility(
        [
            {"stdin": "1", "expected_output": "1"},
            {"stdin": "2", "expected_output": "2"},
            {"stdin": "3", "expected_output": "3"},
        ]
    )
    assert len(vis.sample) == 2 and len(vis.hidden) == 1
    ok = evaluate_coding_reference_verify(
        has_tests=True, has_reference=True, sandbox_error=False, passed=3, total=3
    )
    assert ok.persist is True
    miss = evaluate_coding_reference_verify(
        has_tests=False, has_reference=True, sandbox_error=False, passed=0, total=0
    )
    assert miss.persist is False and miss.retry is True


def test_merge_sd_grade_and_coding_solve_record() -> None:
    from app.services.open_response import (
        heuristic_system_design_grade,
        merge_system_design_grade,
        should_record_coding_solve,
    )

    fallback = heuristic_system_design_grade({"requirements": "x" * 90}, ["cache"]).result
    merged = merge_system_design_grade(
        {
            "mentor_summary": "LLM note",
            "dimensions": [{"key": "api", "score": 4, "note": "good"}],
            "weak_concepts": [],
            "lesson": {},
        },
        fallback,
    )
    assert merged.result["mentor_summary"] == "LLM note"
    assert merged.result["weak_concepts"] == fallback["weak_concepts"]
    api = next(d for d in merged.result["dimensions"] if d["key"] == "api")
    assert api["score"] == 4
    assert should_record_coding_solve(has_subject=True, passed=True)
    assert not should_record_coding_solve(has_subject=True, passed=False)
    assert not should_record_coding_solve(has_subject=False, passed=True)
