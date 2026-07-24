"""Open Response Measurement Engine - mains + interview rubrics.

Design: docs/ENGINES.md (Open Response Measurement)
Version: qb.open_response.v1

Owns: analytic axis rubrics, score clamps, band labels, interview dimension
aggregation. LLM calls / DB / OCR stay in mains.py and interview.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

OPEN_RESPONSE_VERSION = "qb.open_response.v1"
DEFAULT_POLICY = "open_response_v1"

# --- Mains (descriptive analytic rubric) ------------------------------------

MAINS_STRICTNESS = ("exam", "coaching", "gentle")
DEFAULT_MAINS_STRICTNESS = "coaching"

MAINS_AXES: tuple[tuple[str, str], ...] = (
    ("directive", "Directive"),
    ("structure", "Structure"),
    ("coverage", "Coverage"),
    ("substantiation", "Substantiation"),
    ("presentation", "Presentation"),
)
MAINS_AXIS_MAX = 5

MAINS_AXIS_ANCHORS = {
    "directive": (
        "Did the answer DO what the directive demanded (e.g. 'critically examine' "
        "needs a weighed judgement, not mere description)? 0 = ignores it, 2-3 = partly, "
        "5 = fully meets the demand."
    ),
    "structure": (
        "Intro that frames (not restates the question), a logically ordered body, and a "
        "conclusion that adds a verdict/way-forward. 0 = formless, 5 = builds a clear argument."
    ),
    "coverage": (
        "How much of the question's FULL demand and its relevant dimensions are addressed. "
        "0 = one-track/off-topic, 5 = every part + multiple relevant dimensions."
    ),
    "substantiation": (
        "Are claims backed with examples, data, reports, articles, or cases (not bare "
        "assertion)? 0 = unsupported, 5 = well-evidenced throughout."
    ),
    "presentation": (
        "Legibility, headings, crisp expression, near the word limit. Score expression "
        "only; do NOT heavily penalise a content-strong answer here."
    ),
}

# --- Interview (1-4 dimension rubric) ---------------------------------------

INTERVIEW_DIMENSIONS = ("problem_framing", "depth", "tradeoffs", "communication")
INTERVIEW_SCORE_MIN = 1
INTERVIEW_SCORE_MAX = 4


@dataclass(frozen=True)
class OpenResponseVerdict:
    """Typed measurement result with policy provenance."""

    kind: Literal["mains", "interview_typed", "interview_report"]
    result: dict[str, Any]
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_POLICY).strip().lower()
    if p in ("default", "mains", "interview", "rubric"):
        return DEFAULT_POLICY
    return p or DEFAULT_POLICY


def clamp_int(value: Any, lo: int, hi: int, *, default: int | None = None) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return lo if default is None else default


def clamp_interview_score(value: Any) -> int:
    return clamp_int(value, INTERVIEW_SCORE_MIN, INTERVIEW_SCORE_MAX, default=2)


def mains_band(marks: int, marks_max: int, strictness: str = DEFAULT_MAINS_STRICTNESS) -> str:
    pct = (marks / marks_max * 100) if marks_max else 0
    hi, mid, lo = {
        "exam": (75, 60, 42),
        "coaching": (70, 55, 40),
        "gentle": (62, 48, 33),
    }.get(strictness, (70, 55, 40))
    if pct >= hi:
        return "Excellent"
    if pct >= mid:
        return "Good"
    if pct >= lo:
        return "Average"
    return "Needs work"


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v or "").strip()]


def _shape_highlights(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for h in value[:4]:
        if not isinstance(h, dict):
            continue
        quote = str(h.get("quote", "") or "").strip()
        if not quote:
            continue
        kind = str(h.get("kind", "") or "").strip().lower()
        if kind not in ("strong", "weak", "error"):
            kind = "weak"
        out.append(
            {
                "quote": quote,
                "kind": kind,
                "comment": str(h.get("comment", "") or "").strip(),
            }
        )
    return out


def shape_mains_result(
    data: dict[str, Any],
    scheme: dict,
    strictness: str = DEFAULT_MAINS_STRICTNESS,
    *,
    policy: str | None = None,
) -> OpenResponseVerdict:
    """Clamp LLM grade JSON into the mains measurement shape."""
    pol = normalize_policy(policy)
    marks_max = int(scheme.get("marks_max") or 10)
    marks = clamp_int(data.get("marks"), 0, marks_max)
    axis_scores = data.get("axes") if isinstance(data.get("axes"), dict) else {}
    axis_notes = data.get("axis_notes") if isinstance(data.get("axis_notes"), dict) else {}
    axes = [
        {
            "key": key,
            "label": label,
            "score": clamp_int(axis_scores.get(key), 0, MAINS_AXIS_MAX),
            "max": MAINS_AXIS_MAX,
            "comment": str(axis_notes.get(key, "") or "").strip(),
        }
        for key, label in MAINS_AXES
    ]
    hits_by_index = {
        int(h["index"]): bool(h.get("hit"))
        for h in (data.get("scheme_hits") or [])
        if isinstance(h, dict) and isinstance(h.get("index"), (int, float))
    }
    points = [p for p in (scheme.get("model_points") or []) if str(p.get("point", "")).strip()]
    scheme_hits = [
        {
            "point": str(p.get("point", "")).strip(),
            "marks": int(p.get("marks") or 0),
            "hit": hits_by_index.get(i + 1, False),
        }
        for i, p in enumerate(points)
    ]
    result = {
        "marks": marks,
        "marks_max": marks_max,
        "band": mains_band(marks, marks_max, strictness),
        "axes": axes,
        "scheme_hits": scheme_hits,
        "keep_doing": _str_list(data.get("keep_doing"))[:3],
        "improve": _str_list(data.get("improve"))[:3],
        "examiner_note": str(data.get("examiner_note", "") or "").strip(),
        "highlights": _shape_highlights(data.get("highlights")),
    }
    return OpenResponseVerdict(kind="mains", result=result, policy=pol)


def unreadable_mains_result(scheme: dict, *, policy: str | None = None) -> OpenResponseVerdict:
    pol = normalize_policy(policy)
    marks_max = int(scheme.get("marks_max") or 10)
    result = {
        "marks": 0,
        "marks_max": marks_max,
        "band": "Needs work",
        "axes": [
            {"key": k, "label": l, "score": 0, "max": MAINS_AXIS_MAX, "comment": ""}
            for k, l in MAINS_AXES
        ],
        "scheme_hits": [
            {
                "point": str(p.get("point", "")).strip(),
                "marks": int(p.get("marks") or 0),
                "hit": False,
            }
            for p in (scheme.get("model_points") or [])
            if str(p.get("point", "")).strip()
        ],
        "keep_doing": [],
        "improve": [
            "Make sure the answer is legible and in focus, or type it out, then resubmit."
        ],
        "examiner_note": "We couldn't read any answer to grade.",
        "highlights": [],
    }
    return OpenResponseVerdict(kind="mains", result=result, policy=pol)


def shape_interview_scores(
    raw_scores: dict[str, Any] | None,
    *,
    policy: str | None = None,
) -> OpenResponseVerdict:
    pol = normalize_policy(policy)
    src = raw_scores if isinstance(raw_scores, dict) else {}
    scores = {d: clamp_interview_score(src.get(d)) for d in INTERVIEW_DIMENSIONS}
    return OpenResponseVerdict(
        kind="interview_typed",
        result={"scores": scores},
        policy=pol,
    )


def empty_interview_scores(*, policy: str | None = None) -> OpenResponseVerdict:
    pol = normalize_policy(policy)
    scores = {d: INTERVIEW_SCORE_MIN for d in INTERVIEW_DIMENSIONS}
    return OpenResponseVerdict(
        kind="interview_typed",
        result={
            "scores": scores,
            "feedback": (
                "No answer was given, so there's nothing to evaluate. "
                "Try to at least outline your approach next time."
            ),
        },
        policy=pol,
    )


def build_interview_report(
    config: list,
    transcript: list,
    *,
    policy: str | None = None,
) -> OpenResponseVerdict:
    """Per-round + overall score, normalised to a 0-100 scale for display."""
    pol = normalize_policy(policy)
    rounds_out: list[dict[str, Any]] = []
    overall_pcts: list[float] = []
    for rnd in config:
        turns = [t for t in transcript if t.get("round_name") == rnd["name"]]
        if not turns:
            continue
        if rnd["kind"] == "coding":
            passed = sum(t.get("passed", 0) for t in turns)
            total = sum(t.get("total", 0) for t in turns)
            pct = round(100 * passed / total) if total else 0
            detail = f"{passed}/{total} tests passed"
        elif rnd["kind"] == "mcq":
            correct = sum(1 for t in turns if t.get("correct"))
            pct = round(100 * correct / len(turns))
            detail = f"{correct}/{len(turns)} correct"
        else:
            vals = [s for t in turns for s in (t.get("scores") or {}).values()]
            avg = sum(vals) / len(vals) if vals else 0
            pct = round(100 * (avg - 1) / 3) if vals else 0
            detail = f"avg {avg:.1f}/4 across {len(turns)} question(s)"
        rounds_out.append(
            {"name": rnd["name"], "kind": rnd["kind"], "score": pct, "detail": detail}
        )
        overall_pcts.append(pct)
    overall = round(sum(overall_pcts) / len(overall_pcts)) if overall_pcts else 0
    strengths = [r["name"] for r in rounds_out if r["score"] >= 67]
    focus = [r["name"] for r in rounds_out if r["score"] < 50]
    result = {
        "overall": overall,
        "rounds": rounds_out,
        "strengths": strengths,
        "focus_areas": focus,
    }
    return OpenResponseVerdict(kind="interview_report", result=result, policy=pol)
