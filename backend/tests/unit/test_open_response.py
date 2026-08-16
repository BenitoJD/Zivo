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


def test_label_interview_round_band() -> None:
    from app.services.open_response import label_interview_round_band

    assert label_interview_round_band(80) == "strength"
    assert label_interview_round_band(40) == "focus"
    assert label_interview_round_band(55) == "mid"


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
    from app.services.open_response import evaluate_coding_bank_item

    bank = evaluate_coding_bank_item(
        {
            "statement": "s",
            "starter_code": "print(1)",
            "reference_solution": "print(1)",
            "tests": [
                {"stdin": "1", "expected_output": "1"},
                {"stdin": "2", "expected_output": "2"},
                {"stdin": "3", "expected_output": "3"},
            ],
            "title": "Sum",
        }
    )
    assert bank.ok and bank.resolved_title == "Sum"
    from_concept = evaluate_coding_bank_item(
        {
            "statement": "s",
            "starter_code": "print(1)",
            "reference_solution": "print(1)",
            "tests": [
                {"stdin": "1", "expected_output": "1"},
                {"stdin": "2", "expected_output": "2"},
                {"stdin": "3", "expected_output": "3"},
            ],
            "title": "ab",
            "concept": "Two pointers",
        }
    )
    assert from_concept.ok and from_concept.resolved_title == "Two pointers"
    from app.services.open_response import CODING_VERIFY_MAX_ATTEMPTS

    assert CODING_VERIFY_MAX_ATTEMPTS == 3
    from app.services.open_response import evaluate_debug_scenario_qa

    skip = evaluate_debug_scenario_qa(
        has_buggy=False,
        has_fixed=False,
        has_tests=False,
        buggy_fails=False,
        fixed_passes=False,
    )
    assert skip.persist is True and skip.reason == "no_qa_payload"
    ok_debug = evaluate_debug_scenario_qa(
        has_buggy=True,
        has_fixed=True,
        has_tests=True,
        buggy_fails=True,
        fixed_passes=True,
    )
    assert ok_debug.persist is True and ok_debug.reason == "inverse_ok"
    bad_debug = evaluate_debug_scenario_qa(
        has_buggy=True,
        has_fixed=True,
        has_tests=True,
        buggy_fails=False,
        fixed_passes=True,
    )
    assert bad_debug.persist is False and bad_debug.reason == "qa_failed"
    from app.services.open_response import (
        evaluate_debug_cook_input,
        evaluate_debug_scenario_shape,
        plan_debug_cook_input_tokens,
        plan_debug_cook_yield,
        plan_resume_analysis_caps,
        plan_sd_design_caps,
    )

    assert plan_debug_cook_yield(None) == 3
    assert plan_debug_cook_yield(0) == 3
    assert plan_debug_cook_yield(99) == 10
    assert plan_debug_cook_input_tokens() == 6000
    assert evaluate_debug_cook_input(material="x" * 20, brief="").ok
    assert not evaluate_debug_cook_input(material="short", brief="tiny").ok
    assert evaluate_debug_scenario_shape(title="Bug", step_count=1).ok
    assert not evaluate_debug_scenario_shape(title="Bug", step_count=0).ok
    assert evaluate_debug_scenario_shape(title="Bug", step_count=0).reason == "no_steps"
    assert not evaluate_debug_scenario_shape(title="ab", step_count=1).ok
    resume_caps = plan_resume_analysis_caps()
    assert resume_caps.strengths == 6 and resume_caps.improvements == 8
    sd_caps = plan_sd_design_caps()
    assert sd_caps.blocks == 24 and sd_caps.field_chars == 12_000
    from app.services.open_response import (
        plan_coding_assist_sample_display,
        plan_coding_page_input_tokens,
        plan_coding_teach_output_caps,
        plan_debug_cook_store_caps,
        plan_resume_chunk_limit,
        plan_resume_input_tokens,
        plan_resume_jd_input_tokens,
        plan_resume_optimize_caps,
    )

    assert plan_resume_input_tokens() == 6000
    assert plan_resume_jd_input_tokens() == 2000
    assert plan_resume_chunk_limit() == 60
    assert plan_coding_page_input_tokens() == 6000
    assert plan_coding_assist_sample_display() == 4
    store = plan_debug_cook_store_caps()
    assert store.material == 100_000 and store.brief == 4_000
    opt = plan_resume_optimize_caps()
    assert opt.bullets == 20 and opt.missing_keywords == 20
    teach = plan_coding_teach_output_caps()
    assert teach.statement == 1200 and teach.source == 2500
    assert teach.weak_concepts == 3 and teach.title == 120
    assert teach.body == 2000 and teach.try_this == 400
    assert teach.focus_tags == 6


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
    api = next(filter(lambda d: d["key"] == "api", merged.result["dimensions"]))
    assert api["score"] == 4
    assert should_record_coding_solve(has_subject=True, passed=True)
    assert not should_record_coding_solve(has_subject=True, passed=False)
    assert not should_record_coding_solve(has_subject=False, passed=True)


def test_resume_ats_checks_and_blend() -> None:
    from app.services.open_response import (
        evaluate_resume_deterministic_checks,
        plan_resume_ats_score,
    )

    good = (
        "Jane Doe\njane@example.com  +1 415 555 1234\n"
        "Experience\n- Led a team that improved latency by 40%\n- Built and shipped 3 services\n"
        "- Reduced costs by 25% and increased signups 2x\nEducation\nBS CS, MIT\nSkills\nPython, Go, SQL"
    )
    checks = evaluate_resume_deterministic_checks(good)
    assert sum(c["pass"] for c in checks) >= 8
    bad = "i am a hard working person and i want a job. my email is missing."
    assert sum(c["pass"] for c in evaluate_resume_deterministic_checks(bad)) <= 4
    assert plan_resume_ats_score(det_score=80, content_score=60) == 70


def test_mcq_grade_kind_and_correctness() -> None:
    from app.services.open_response import evaluate_mcq_correct, evaluate_mcq_grade_kind

    assert evaluate_mcq_grade_kind(is_multi=False) == "single"
    assert evaluate_mcq_grade_kind(is_multi=True) == "multi"
    assert evaluate_mcq_correct(
        is_multi=False, choice_index=1, correct_index=1
    )
    assert not evaluate_mcq_correct(
        is_multi=False, choice_index=0, correct_index=1
    )
    assert evaluate_mcq_correct(
        is_multi=True,
        choice_index=0,
        correct_index=0,
        choice_indices=[0, 2],
        correct_indices=[0, 2],
    )
    assert not evaluate_mcq_correct(
        is_multi=True,
        choice_index=0,
        correct_index=0,
        choice_indices=[0],
        correct_indices=[0, 2],
    )
