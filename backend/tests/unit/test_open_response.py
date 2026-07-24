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
    assert v.result["scores"]["depth"] == 4
    assert v.result["scores"]["tradeoffs"] == 2
