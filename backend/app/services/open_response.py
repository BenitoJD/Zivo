"""Open Response Measurement Engine - mains + interview rubrics.

Design: docs/ENGINES.md (Open Response Measurement)
Version: qb.open_response.v1

Owns: analytic axis rubrics, score clamps, band labels, interview dimension
aggregation. LLM calls / DB / OCR stay in mains.py and interview.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick

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
INTERVIEW_STRENGTH_PCT = 67
INTERVIEW_FOCUS_PCT = 50

InterviewRoundBand = Literal["strength", "focus", "mid"]


_BAND_RULES = (
    Rule(when=(Pred("strength", "truthy"),), action="strength"),
    Rule(when=(Pred("focus", "truthy"),), action="focus"),
    Rule(when=(), action="mid"),
)
_POLICY_ALIASES = ("default", "mains", "interview", "rubric")
_MAINS_BAND_RULES = (
    Rule(when=(Pred("excellent", "truthy"),), action="Excellent"),
    Rule(when=(Pred("good", "truthy"),), action="Good"),
    Rule(when=(Pred("average", "truthy"),), action="Average"),
    Rule(when=(), action="Needs work"),
)
_CODING_VERIFY_RULES = (
    Rule(
        when=(Pred("missing", "truthy"),),
        action="missing_tests_or_reference",
        extras={"persist": False, "retry": True},
    ),
    Rule(
        when=(Pred("sandbox", "truthy"),),
        action="sandbox_error",
        extras={"persist": False, "retry": True},
    ),
    Rule(
        when=(Pred("all_pass", "truthy"),),
        action="passed",
        extras={"persist": True, "retry": False},
    ),
    Rule(
        when=(),
        action="failed_tests",
        extras={"persist": False, "retry": True},
    ),
)
_DEBUG_QA_RULES = (
    Rule(when=(Pred("no_payload", "truthy"),), action="no_qa_payload", extras={"persist": True}),
    Rule(when=(Pred("inverse_ok", "truthy"),), action="inverse_ok", extras={"persist": True}),
    Rule(when=(), action="qa_failed", extras={"persist": False}),
)
_BANK_RULES = (
    Rule(when=(Pred("missing_required", "truthy"),), action="missing_required_fields"),
    Rule(when=(Pred("no_tests", "truthy"),), action="no_tests"),
    Rule(when=(Pred("no_hidden", "truthy"),), action="no_hidden_tests"),
    Rule(when=(Pred("short_title", "truthy"),), action="title_too_short"),
    Rule(when=(), action="ok"),
)


def label_interview_round_band(score: int) -> InterviewRoundBand:
    """Band a mock-interview round percent for strengths vs focus areas."""
    pct = int(score)
    hit = first_match(
        _BAND_RULES,
        {
            "strength": pct >= INTERVIEW_STRENGTH_PCT,
            "focus": pct < INTERVIEW_FOCUS_PCT,
        },
    )
    return hit.action  # type: ignore[return-value]


@dataclass(frozen=True)
class OpenResponseVerdict:
    """Typed measurement result with policy provenance."""

    kind: Literal[
        "mains",
        "interview_typed",
        "interview_report",
        "interview_coding",
        "coding_teach",
        "system_design",
    ]
    result: dict[str, Any]
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


def normalize_policy(policy: str | None) -> str:
    p = (policy or DEFAULT_POLICY).strip().lower()
    return pick(p in _POLICY_ALIASES, lambda: DEFAULT_POLICY, lambda: p or DEFAULT_POLICY)


def clamp_int(value: Any, lo: int, hi: int, *, default: int | None = None) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return choose(default is None, lo, default)


def clamp_interview_score(value: Any) -> int:
    return clamp_int(value, INTERVIEW_SCORE_MIN, INTERVIEW_SCORE_MAX, default=2)


@dataclass(frozen=True)
class MainsAttemptPlan:
    strictness: str
    marks_max: int
    word_target: int
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


def plan_mains_attempt(
    *,
    strictness: str,
    marks_max: int,
    policy: str | None = None,
) -> MainsAttemptPlan:
    """Clamp mains marks band and matching word target."""
    pol = normalize_policy(policy)
    s = choose(strictness in MAINS_STRICTNESS, strictness, DEFAULT_MAINS_STRICTNESS)
    marks = choose(int(marks_max or 10) >= 13, 15, 10)
    words = choose(marks >= 13, 250, 150)
    return MainsAttemptPlan(strictness=s, marks_max=marks, word_target=words, policy=pol)


def mains_band(marks: int, marks_max: int, strictness: str = DEFAULT_MAINS_STRICTNESS) -> str:
    pct = pick(bool(marks_max), lambda: marks / marks_max * 100, lambda: 0)
    hi, mid, lo = {
        "exam": (75, 60, 42),
        "coaching": (70, 55, 40),
        "gentle": (62, 48, 33),
    }.get(strictness, (70, 55, 40))
    hit = first_match(
        _MAINS_BAND_RULES,
        {
            "excellent": pct >= hi,
            "good": pct >= mid,
            "average": pct >= lo,
        },
    )
    return hit.action


def _str_list(value: Any) -> list[str]:
    return pick(
        not isinstance(value, list),
        lambda: [],
        lambda: list(filter(None, (str(v or "").strip() for v in value))),
    )


def _shape_highlights(value: Any) -> list[dict[str, str]]:
    def one(h: Any) -> dict[str, str] | None:
        return pick(
            not isinstance(h, dict),
            lambda: None,
            lambda: pick(
                not str(h.get("quote", "") or "").strip(),
                lambda: None,
                lambda: {
                    "quote": str(h.get("quote", "") or "").strip(),
                    "kind": choose(
                        str(h.get("kind", "") or "").strip().lower() in ("strong", "weak", "error"),
                        str(h.get("kind", "") or "").strip().lower(),
                        "weak",
                    ),
                    "comment": str(h.get("comment", "") or "").strip(),
                },
            ),
        )

    return pick(
        not isinstance(value, list),
        lambda: [],
        lambda: list(filter(None, map(one, value[:4]))),
    )


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
    axis_scores = pick(
        isinstance(data.get("axes"), dict),
        lambda: data.get("axes"),
        lambda: {},
    )
    axis_notes = pick(
        isinstance(data.get("axis_notes"), dict),
        lambda: data.get("axis_notes"),
        lambda: {},
    )
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
        for h in filter(
            lambda h: isinstance(h, dict) and isinstance(h.get("index"), (int, float)),
            data.get("scheme_hits") or [],
        )
    }
    points = list(
        filter(
            lambda p: str(p.get("point", "")).strip(),
            scheme.get("model_points") or [],
        )
    )
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
            for p in filter(
                lambda p: str(p.get("point", "")).strip(),
                scheme.get("model_points") or [],
            )
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
    src = pick(isinstance(raw_scores, dict), lambda: raw_scores, lambda: {})
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


def degraded_interview_scores(*, policy: str | None = None) -> OpenResponseVerdict:
    """Mid-scale scores when the interview eval LLM is unavailable."""
    pol = normalize_policy(policy)
    mid = (INTERVIEW_SCORE_MIN + INTERVIEW_SCORE_MAX) // 2
    scores = {d: mid for d in INTERVIEW_DIMENSIONS}
    return OpenResponseVerdict(
        kind="interview_typed",
        result={
            "scores": scores,
            "feedback": "Answer recorded (automatic scoring was unavailable for this one).",
        },
        policy=pol,
    )


def evaluate_interview_coding_turn(
    *,
    source: str,
    language_id: int,
    passed: int,
    total: int,
    error: str | None = None,
    first_fail: dict[str, Any] | None = None,
    policy: str | None = None,
) -> OpenResponseVerdict:
    """Pass/fail + feedback for an interview coding turn. Runner stays plumbing."""
    pol = normalize_policy(policy)
    ok = bool(total) and int(passed) == int(total)

    def fail_feedback() -> str:
        def hint() -> str:
            detail = first_fail.get("stderr") or (
                f"got {first_fail.get('stdout')!r}, expected {first_fail.get('expected')!r}"
            )
            return f" First failing case: {detail}"

        extra = pick(bool(first_fail), hint, lambda: "")
        return f"{passed}/{total} tests passed.{extra}"

    feedback = pick(
        bool(error),
        lambda: f"Couldn't run your code: {error}",
        lambda: pick(
            ok,
            lambda: f"All {total} tests passed — clean solution.",
            fail_feedback,
        ),
    )
    return OpenResponseVerdict(
        kind="interview_coding",
        result={
            "answer": source,
            "language_id": language_id,
            "passed": int(passed),
            "total": int(total),
            "correct": ok,
            "feedback": feedback,
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

    def round_row(rnd: dict[str, Any]) -> dict[str, Any] | None:
        turns = list(filter(lambda t: t.get("round_name") == rnd["name"], transcript))

        def coding_row() -> dict[str, Any]:
            passed = sum(t.get("passed", 0) for t in turns)
            total = sum(t.get("total", 0) for t in turns)
            pct = pick(bool(total), lambda: round(100 * passed / total), lambda: 0)
            return {
                "name": rnd["name"],
                "kind": rnd["kind"],
                "score": pct,
                "detail": f"{passed}/{total} tests passed",
            }

        def mcq_row() -> dict[str, Any]:
            correct = sum(1 for t in filter(lambda t: t.get("correct"), turns))
            pct = round(100 * correct / len(turns))
            return {
                "name": rnd["name"],
                "kind": rnd["kind"],
                "score": pct,
                "detail": f"{correct}/{len(turns)} correct",
            }

        def typed_row() -> dict[str, Any]:
            vals = [s for t in turns for s in (t.get("scores") or {}).values()]
            avg = pick(bool(vals), lambda: sum(vals) / len(vals), lambda: 0)
            pct = pick(bool(vals), lambda: round(100 * (avg - 1) / 3), lambda: 0)
            return {
                "name": rnd["name"],
                "kind": rnd["kind"],
                "score": pct,
                "detail": f"avg {avg:.1f}/4 across {len(turns)} question(s)",
            }

        kind_key = choose(rnd["kind"] in ("coding", "mcq"), rnd["kind"], "typed")
        return pick(
            not turns,
            lambda: None,
            lambda: apply(kind_key, {"coding": coding_row, "mcq": mcq_row, "typed": typed_row}),
        )

    rounds_out = list(filter(None, map(round_row, config)))
    overall_pcts = [r["score"] for r in rounds_out]
    overall = pick(
        bool(overall_pcts),
        lambda: round(sum(overall_pcts) / len(overall_pcts)),
        lambda: 0,
    )
    strengths = [
        r["name"]
        for r in filter(
            lambda r: label_interview_round_band(r["score"]) == "strength",
            rounds_out,
        )
    ]
    focus = [
        r["name"]
        for r in filter(
            lambda r: label_interview_round_band(r["score"]) == "focus",
            rounds_out,
        )
    ]
    result = {
        "overall": overall,
        "rounds": rounds_out,
        "strengths": strengths,
        "focus_areas": focus,
    }
    return OpenResponseVerdict(kind="interview_report", result=result, policy=pol)


# --- Coding teach-gap (heuristic mentor lesson) ------------------------------


def _normalize_coding_focus(tags: list[str], concept: str) -> list[str]:
    out: list[str] = []
    for t in tags:
        s = str(t).strip().lower()
        out += choose(bool(s) and s not in out, [s], [])
    c = str(concept or "").strip().lower()
    out += choose(bool(c) and c not in out, [c], [])
    return out[:6]


def heuristic_coding_teach_gap(
    *,
    all_passed: bool,
    passed: int,
    total: int,
    tags: list[str],
    concept: str,
    first_fail: dict[str, Any] | None,
    policy: str | None = None,
) -> OpenResponseVerdict:
    """Deterministic mentor + lesson when the coding teach LLM is unavailable.

    Owns lesson shape / weak-concept pick. LLM + cache stay in ``coding_teach_gap``.
    """
    pol = normalize_policy(policy)
    focus = _normalize_coding_focus(tags, concept)
    weak = focus[:2] or ["edge-cases"]

    def fail_hint() -> str:
        stderr = str(first_fail.get("stderr") or "").strip()
        return pick(
            bool(stderr),
            lambda: " Runtime/compile noise showed up — fix that before chasing logic.",
            lambda: pick(
                first_fail.get("expected") is not None,
                lambda: " Your output diverged from the expected case — check boundaries and off-by-one.",
                lambda: "",
            ),
        )

    def fail_result() -> dict[str, Any]:
        hint = pick(bool(first_fail), fail_hint, lambda: "")
        ratio = pick(bool(total), lambda: f"{passed}/{total}", lambda: "0/0")
        return {
            "mentor_summary": (
                f"You cleared {ratio} hidden tests.{hint} "
                "The gap is usually one missed invariant, not more code."
            ),
            "weak_concepts": weak,
            "lesson": {
                "title": "Read the failing case as a clue",
                "body": (
                    "A single failing input usually points at a boundary you skipped: empty, one element, "
                    "duplicates, or the last index. Restate the invariant the solution must keep, then fix that."
                ),
                "try_this": "Before re-submitting, write the invariant in one line above your loop.",
            },
        }

    pass_result = {
        "mentor_summary": (
            "Tests are green. The next edge is applying the same pattern under a twist — "
            "constraints change, or the data structure choice gets costly."
        ),
        "weak_concepts": weak,
        "lesson": {
            "title": "Own the pattern, then stretch it",
            "body": (
                "Passing tests means the happy path works. Solid mastery is recognizing when "
                "the same idea needs a different cut of the input or a tighter bound."
            ),
            "try_this": "On the next problem, name the pattern in one sentence before coding.",
        },
    }
    result = pick(all_passed, lambda: pass_result, fail_result)
    return OpenResponseVerdict(kind="coding_teach", result=result, policy=pol)


# --- System design heuristic grade -------------------------------------------

SD_DIMENSIONS = ("framing", "api", "data", "scale", "tradeoffs", "communication")


def heuristic_system_design_grade(
    design: dict[str, Any],
    concept_keys: list[str],
    *,
    policy: str | None = None,
) -> OpenResponseVerdict:
    """Deterministic SD mentor when the grade LLM is unavailable."""
    import re

    pol = normalize_policy(policy)
    text_blob = " ".join(
        str(design.get(k) or "") for k in ("requirements", "apis", "data", "scale")
    )
    words = len(re.findall(r"\w+", text_blob))
    blocks = design.get("blocks") or []
    base = pick(words > 80, lambda: 3, lambda: pick(words < 25, lambda: 1, lambda: 2))
    low = text_blob.lower()

    def dim_score(key: str) -> dict[str, Any]:
        bumped = min(4, base + 1)
        score = apply(
            key,
            {
                "api": lambda: choose("api" in low or "endpoint" in low, bumped, base),
                "data": lambda: choose(
                    any(w in low for w in ("db", "database", "sql", "store")),
                    bumped,
                    base,
                ),
                "scale": lambda: choose(
                    any(w in low for w in ("cache", "shard", "qps", "cdn", "queue")),
                    bumped,
                    base,
                ),
                "framing": lambda: choose(words > 40, min(4, max(base, 2)), base),
                "communication": lambda: choose(bool(blocks), bumped, base),
                "tradeoffs": lambda: base,
            },
        )
        return {"key": key, "score": score, "note": "Heuristic score - model unavailable."}

    dims = [dim_score(key) for key in SD_DIMENSIONS]
    weak = list(concept_keys[:1]) or ["requirements"]
    result = {
        "mentor_summary": (
            "You sketched a direction, but the interesting constraints are still thin. "
            "Name the hot path, the data ownership, and one failure mode before drawing more boxes."
        ),
        "dimensions": dims,
        "weak_concepts": weak,
        "lesson": {
            "title": "Start from the hot path",
            "body": (
                "Great designs begin with the request that happens most often and the data it "
                "must touch. Write that path end-to-end before optimizing side features."
            ),
            "try_this": "On the next case, write the single most common request as a numbered sequence of hops.",
        },
    }
    return OpenResponseVerdict(kind="system_design", result=result, policy=pol)


def merge_system_design_grade(
    llm: dict[str, Any],
    fallback: dict[str, Any],
    *,
    policy: str | None = None,
) -> OpenResponseVerdict:
    """Clamp LLM SD dims and fill gaps from the heuristic fallback."""
    pol = normalize_policy(policy)
    dims_in = llm.get("dimensions") or []
    dims = []
    for key in SD_DIMENSIONS:
        found = next(
            filter(lambda d: isinstance(d, dict) and d.get("key") == key, dims_in),
            None,
        )
        dims.append(
            {
                "key": key,
                "score": clamp_interview_score((found or {}).get("score")),
                "note": str((found or {}).get("note") or "")[:280],
            }
        )
    weak = [str(w) for w in filter(lambda w: str(w), llm.get("weak_concepts") or [])][:3]
    weak = pick(not weak, lambda: list(fallback.get("weak_concepts") or []), lambda: weak)
    lesson_in = pick(
        isinstance(llm.get("lesson"), dict),
        lambda: llm.get("lesson"),
        lambda: {},
    )
    fb_lesson = pick(
        isinstance(fallback.get("lesson"), dict),
        lambda: fallback.get("lesson"),
        lambda: {},
    )
    result = {
        "mentor_summary": str(llm.get("mentor_summary") or "").strip()
        or str(fallback.get("mentor_summary") or ""),
        "dimensions": dims,
        "weak_concepts": weak,
        "lesson": {
            "title": str(lesson_in.get("title") or fb_lesson.get("title") or "")[:120],
            "body": str(lesson_in.get("body") or fb_lesson.get("body") or "")[:2000],
            "try_this": str(lesson_in.get("try_this") or fb_lesson.get("try_this") or "")[:400],
        },
    }
    return OpenResponseVerdict(kind="system_design", result=result, policy=pol)


def should_record_coding_solve(*, has_subject: bool, passed: bool) -> bool:
    """Persist measurement only on the first full pass, never on a fail."""
    return bool(has_subject) and bool(passed)


# --- Coding bank structural gate ---------------------------------------------


@dataclass(frozen=True)
class CodingBankVerdict:
    ok: bool
    reason: str
    resolved_title: str = ""
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


def evaluate_coding_bank_item(
    problem: dict[str, Any],
    *,
    min_title_len: int = 3,
    require_hidden_tests: bool = True,
    policy: str | None = None,
) -> CodingBankVerdict:
    """Whether a generated coding problem is bank-grade enough to persist.

    Judge0 verify / LLM stay in coding_generation; this owns structural thresholds.
    """
    pol = normalize_policy(policy)
    required = ("statement", "starter_code", "reference_solution", "tests")
    tests = problem.get("tests")
    clean = list(
        filter(
            lambda t: isinstance(t, dict),
            pick(isinstance(tests, list), lambda: tests, lambda: []),
        )
    )
    title = str(problem.get("title") or "").strip()
    title = pick(
        len(title) < min_title_len,
        lambda: str(problem.get("concept") or "").strip(),
        lambda: title,
    )
    hit = first_match(
        _BANK_RULES,
        {
            "missing_required": not all(problem.get(k) for k in required),
            "no_tests": not isinstance(tests, list) or not tests,
            "no_hidden": require_hidden_tests and len(clean) <= 2,
            "short_title": len(title) < min_title_len,
        },
    )
    return CodingBankVerdict(
        hit.action == "ok",
        hit.action,
        resolved_title=choose(hit.action == "ok", title, ""),
        policy=pol,
    )


CODING_SAMPLE_TEST_COUNT = 2


@dataclass(frozen=True)
class CodingTestVisibilityPlan:
    sample: list[dict[str, str]]
    hidden: list[dict[str, str]]
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


def plan_coding_test_visibility(
    tests: list[dict[str, Any]] | None,
    *,
    sample_n: int = CODING_SAMPLE_TEST_COUNT,
    policy: str | None = None,
) -> CodingTestVisibilityPlan:
    """First N cases are public samples; the rest stay hidden."""
    pol = normalize_policy(policy)
    n = max(1, int(sample_n))
    clean = [
        {
            "stdin": str(t.get("stdin", "")),
            "expected_output": str(t.get("expected_output", "")),
        }
        for t in filter(lambda t: isinstance(t, dict), tests or [])
    ]
    return CodingTestVisibilityPlan(clean[:n], clean[n:], policy=pol)


@dataclass(frozen=True)
class CodingVerifyVerdict:
    persist: bool
    retry: bool
    reason: str
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


# Retry budget for generate-and-verify. Loop stays plumbing; the cap is policy.
CODING_VERIFY_MAX_ATTEMPTS = 3


def evaluate_coding_reference_verify(
    *,
    has_tests: bool,
    has_reference: bool,
    sandbox_error: bool,
    passed: int,
    total: int,
    policy: str | None = None,
) -> CodingVerifyVerdict:
    """Pass/fail of one generate-and-verify attempt. Retry loop stays plumbing."""
    pol = normalize_policy(policy)
    hit = first_match(
        _CODING_VERIFY_RULES,
        {
            "missing": not has_tests or not has_reference,
            "sandbox": sandbox_error,
            "all_pass": bool(int(total)) and int(passed) == int(total),
        },
    )
    return CodingVerifyVerdict(
        bool(hit.extras["persist"]),
        bool(hit.extras["retry"]),
        hit.action,
        policy=pol,
    )


@dataclass(frozen=True)
class DebugScenarioQaVerdict:
    persist: bool
    reason: str
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


def evaluate_debug_scenario_qa(
    *,
    has_buggy: bool,
    has_fixed: bool,
    has_tests: bool,
    buggy_fails: bool,
    fixed_passes: bool,
    policy: str | None = None,
) -> DebugScenarioQaVerdict:
    """Persist a debug cook only when buggy code fails tests and the fix passes.

    Missing cook_qa is not a reject: the model may omit it for non-code scenarios.
    """
    pol = normalize_policy(policy)
    hit = first_match(
        _DEBUG_QA_RULES,
        {
            "no_payload": not has_buggy or not has_fixed or not has_tests,
            "inverse_ok": buggy_fails and fixed_passes,
        },
    )
    return DebugScenarioQaVerdict(bool(hit.extras["persist"]), hit.action, policy=pol)


DEBUG_COOK_DEFAULT_COUNT = 3
DEBUG_COOK_MAX_COUNT = 10
DEBUG_COOK_MIN_MATERIAL_CHARS = 20
DEBUG_COOK_MIN_BRIEF_CHARS = 10
DEBUG_COOK_INPUT_MAX_TOKENS = 6000
DEBUG_COOK_MATERIAL_CHARS = 100_000
DEBUG_COOK_BRIEF_CHARS = 4_000
DEBUG_TITLE_MIN_CHARS = 3
DEBUG_MIN_STEPS = 1


def plan_debug_cook_yield(requested: int | None = None) -> int:
    """How many debug scenarios one cook job may produce."""
    n = pick(not requested, lambda: DEBUG_COOK_DEFAULT_COUNT, lambda: int(requested))
    return max(1, min(n, DEBUG_COOK_MAX_COUNT))


def plan_debug_cook_input_tokens() -> int:
    """How much source material the debug cook LLM may see."""
    return DEBUG_COOK_INPUT_MAX_TOKENS


@dataclass(frozen=True)
class DebugCookStoreCaps:
    material: int
    brief: int
    policy_version: str = OPEN_RESPONSE_VERSION


def plan_debug_cook_store_caps() -> DebugCookStoreCaps:
    """How large a debug cook job's stored material and brief may be."""
    return DebugCookStoreCaps(DEBUG_COOK_MATERIAL_CHARS, DEBUG_COOK_BRIEF_CHARS)


@dataclass(frozen=True)
class DebugCookInputVerdict:
    ok: bool
    reason: str
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


def evaluate_debug_cook_input(
    *,
    material: str,
    brief: str = "",
    policy: str | None = None,
) -> DebugCookInputVerdict:
    """Reject a debug cook that has neither usable material nor a brief."""
    pol = normalize_policy(policy)
    has_material = len((material or "").strip()) >= DEBUG_COOK_MIN_MATERIAL_CHARS
    has_brief = len((brief or "").strip()) >= DEBUG_COOK_MIN_BRIEF_CHARS
    return pick(
        has_material or has_brief,
        lambda: DebugCookInputVerdict(True, "ok", policy=pol),
        lambda: DebugCookInputVerdict(False, "too_thin", policy=pol),
    )


@dataclass(frozen=True)
class DebugScenarioShapeVerdict:
    ok: bool
    reason: str
    policy: str = DEFAULT_POLICY
    policy_version: str = OPEN_RESPONSE_VERSION


def evaluate_debug_scenario_shape(
    *,
    title: str,
    step_count: int,
    min_title_len: int = DEBUG_TITLE_MIN_CHARS,
    min_steps: int = DEBUG_MIN_STEPS,
    policy: str | None = None,
) -> DebugScenarioShapeVerdict:
    """Structural persist bar for a debug scenario (title + at least one step)."""
    pol = normalize_policy(policy)
    return pick(
        int(step_count) < int(min_steps),
        lambda: DebugScenarioShapeVerdict(False, "no_steps", policy=pol),
        lambda: pick(
            len((title or "").strip()) < int(min_title_len),
            lambda: DebugScenarioShapeVerdict(False, "title_too_short", policy=pol),
            lambda: DebugScenarioShapeVerdict(True, "ok", policy=pol),
        ),
    )


RESUME_STRENGTHS_MAX = 6
RESUME_IMPROVEMENTS_MAX = 8
RESUME_OPTIMIZE_BULLETS_MAX = 20
RESUME_OPTIMIZE_KEYWORDS_MAX = 20
RESUME_INPUT_MAX_TOKENS = 6000
RESUME_JD_INPUT_MAX_TOKENS = 2000
RESUME_CHUNK_LIMIT = 60
CODING_PAGE_INPUT_MAX_TOKENS = 6000
CODING_ASSIST_SAMPLE_DISPLAY = 4
SD_DESIGN_BLOCKS_MAX = 24
SD_DESIGN_FIELD_MAX_CHARS = 12_000
CODING_TEACH_STATEMENT_CHARS = 1200
CODING_TEACH_SOURCE_CHARS = 2500
CODING_TEACH_WEAK_MAX = 3
CODING_TEACH_TITLE_CHARS = 120
CODING_TEACH_BODY_CHARS = 2000
CODING_TEACH_TRY_THIS_CHARS = 400
CODING_TEACH_FOCUS_TAGS = 6


@dataclass(frozen=True)
class ResumeAnalysisCaps:
    strengths: int
    improvements: int
    policy_version: str = OPEN_RESPONSE_VERSION


def plan_resume_analysis_caps() -> ResumeAnalysisCaps:
    """How many ATS strengths / improvements to keep from the LLM review."""
    return ResumeAnalysisCaps(RESUME_STRENGTHS_MAX, RESUME_IMPROVEMENTS_MAX)


@dataclass(frozen=True)
class ResumeOptimizeCaps:
    bullets: int
    missing_keywords: int
    policy_version: str = OPEN_RESPONSE_VERSION


def plan_resume_optimize_caps() -> ResumeOptimizeCaps:
    """How many rewrite bullets and missing keywords the optimizer may keep."""
    return ResumeOptimizeCaps(RESUME_OPTIMIZE_BULLETS_MAX, RESUME_OPTIMIZE_KEYWORDS_MAX)


def plan_resume_input_tokens() -> int:
    """How much resume text ATS analysis and interview context may send to the LLM."""
    return RESUME_INPUT_MAX_TOKENS


def plan_resume_jd_input_tokens() -> int:
    """How much job-description text the resume optimizer may send to the LLM."""
    return RESUME_JD_INPUT_MAX_TOKENS


def plan_resume_chunk_limit() -> int:
    """How many ingested chunks to prefetch before resume/interview token trim."""
    return RESUME_CHUNK_LIMIT


def plan_coding_page_input_tokens() -> int:
    """How much page text a coding-problem cook may send to the LLM."""
    return CODING_PAGE_INPUT_MAX_TOKENS


def plan_coding_assist_sample_display() -> int:
    """How many sample tests the coding-assist tutor prompt may include."""
    return CODING_ASSIST_SAMPLE_DISPLAY


@dataclass(frozen=True)
class CodingTeachOutputCaps:
    statement: int
    source: int
    weak_concepts: int
    title: int
    body: int
    try_this: int
    focus_tags: int
    policy_version: str = OPEN_RESPONSE_VERSION


def plan_coding_teach_output_caps() -> CodingTeachOutputCaps:
    """Truncation caps for coding teach-gap LLM input and cached lesson output."""
    return CodingTeachOutputCaps(
        CODING_TEACH_STATEMENT_CHARS,
        CODING_TEACH_SOURCE_CHARS,
        CODING_TEACH_WEAK_MAX,
        CODING_TEACH_TITLE_CHARS,
        CODING_TEACH_BODY_CHARS,
        CODING_TEACH_TRY_THIS_CHARS,
        CODING_TEACH_FOCUS_TAGS,
    )


@dataclass(frozen=True)
class SdDesignCaps:
    blocks: int
    field_chars: int
    policy_version: str = OPEN_RESPONSE_VERSION


def plan_sd_design_caps() -> SdDesignCaps:
    """How large a saved system-design draft may be."""
    return SdDesignCaps(SD_DESIGN_BLOCKS_MAX, SD_DESIGN_FIELD_MAX_CHARS)


RESUME_WORD_MIN = 250
RESUME_WORD_MAX = 900
RESUME_MIN_BULLETS = 3
RESUME_MIN_METRICS = 3
RESUME_MIN_ACTION_VERBS = 4
RESUME_DET_WEIGHT = 0.5
RESUME_CONTENT_WEIGHT = 0.5

_RESUME_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESUME_PHONE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_RESUME_ACTION_VERBS = {
    "led", "built", "designed", "developed", "created", "launched", "improved", "increased",
    "reduced", "managed", "shipped", "implemented", "optimized", "delivered", "owned",
    "drove", "architected", "automated", "scaled", "migrated", "spearheaded",
}
_RESUME_SECTIONS = {
    "experience": ("experience", "work history", "employment"),
    "education": ("education",),
    "skills": ("skills", "technical skills", "technologies"),
}


def evaluate_resume_deterministic_checks(resume: str) -> list[dict[str, Any]]:
    """Mechanical ATS checks (contact, sections, length, bullets, metrics, voice)."""
    low = resume.lower()
    words = resume.split()
    wc = len(words)
    bullets = sum(low.count(b) for b in ("•", "- ", "* ", "▪"))
    quantified = len(re.findall(r"\b\d+%?\b", resume))
    action_hits = sum(1 for w in filter(lambda w: w in low, _RESUME_ACTION_VERBS))
    first_person = sum(low.count(p) for p in (" i ", " my ", " me "))
    has_email = bool(_RESUME_EMAIL.search(resume))
    has_phone = bool(_RESUME_PHONE.search(resume))
    has_exp = any(s in low for s in _RESUME_SECTIONS["experience"])
    has_edu = any(s in low for s in _RESUME_SECTIONS["education"])
    has_skills = any(s in low for s in _RESUME_SECTIONS["skills"])
    length_ok = RESUME_WORD_MIN <= wc <= RESUME_WORD_MAX
    checks = [
        ("Contact email", has_email, choose(has_email, "A parseable email address is present.", "No email found: ATS needs one to contact you.")),
        ("Phone number", has_phone, choose(has_phone, "Phone number present.", "Add a phone number.")),
        ("Experience section", has_exp, choose(has_exp, "Found a work-experience section.", "Add a clearly labelled Experience section.")),
        ("Education section", has_edu, choose(has_edu, "Found an education section.", "Add an Education section.")),
        ("Skills section", has_skills, choose(has_skills, "Found a skills section.", "Add a Skills section with relevant keywords.")),
        ("Reasonable length", length_ok, choose(length_ok, f"{wc} words, good length.", f"{wc} words: aim for ~1 page ({RESUME_WORD_MIN}-{RESUME_WORD_MAX} words).")),
        ("Bullet points", bullets >= RESUME_MIN_BULLETS, choose(bullets >= RESUME_MIN_BULLETS, f"{bullets} bullet points.", "Use bullet points for achievements, not paragraphs.")),
        ("Quantified impact", quantified >= RESUME_MIN_METRICS, choose(quantified >= RESUME_MIN_METRICS, f"{quantified} numbers/metrics found.", "Quantify achievements (%, $, counts): ATS and recruiters reward metrics.")),
        ("Strong action verbs", action_hits >= RESUME_MIN_ACTION_VERBS, choose(action_hits >= RESUME_MIN_ACTION_VERBS, f"{action_hits} action verbs.", "Start bullets with action verbs (Led, Built, Improved).")),
        ("Third-person voice", first_person == 0, choose(first_person == 0, "No first-person pronouns.", "Drop first-person pronouns (I, my): resumes are written impersonally.")),
    ]
    return [{"name": n, "pass": bool(p), "detail": d} for (n, p, d) in checks]


def plan_resume_ats_score(*, det_score: int, content_score: int) -> int:
    """Blend mechanical ATS checks with the LLM content review."""
    return round(RESUME_DET_WEIGHT * int(det_score) + RESUME_CONTENT_WEIGHT * int(content_score))


CODING_DIFFICULTIES = ("easy", "medium", "hard")
DEFAULT_CODING_DIFFICULTY = "medium"


def resolve_coding_language_id(
    language_id: Any,
    known: Mapping[Any, Any],
    *,
    default: int,
) -> int:
    """Bank/interview coding language: keep a known id, else the default."""
    lid = int(language_id or default)
    return choose(lid in known, lid, default)


def normalize_coding_difficulty(raw: Any) -> str:
    """Clamp a coding-bank difficulty label to easy/medium/hard."""
    d = str(raw or DEFAULT_CODING_DIFFICULTY).strip().lower() or DEFAULT_CODING_DIFFICULTY
    return choose(d in CODING_DIFFICULTIES, d, DEFAULT_CODING_DIFFICULTY)


_INTERVIEW_TURN_RULES = (
    Rule(when=(Pred("kind", "eq", "coding"),), action="coding"),
    Rule(when=(Pred("kind", "eq", "mcq"),), action="mcq"),
    Rule(when=(), action="typed"),
)


def evaluate_interview_turn_kind(kind: str) -> str:
    """Dispatch an interview answer onto coding, MCQ, or typed rubric scoring."""
    return first_match(_INTERVIEW_TURN_RULES, {"kind": str(kind or "")}).action


_SD_SUBJECT_RULES = (
    Rule(when=(Pred("has_account", "truthy"),), action="account"),
    Rule(when=(), action="guest"),
)


def evaluate_sd_subject(*, account_id: Any) -> str:
    """System-design session rows are keyed by account or guest."""
    return first_match(_SD_SUBJECT_RULES, {"has_account": account_id is not None}).action


_MCQ_GRADE_KIND_RULES = (
    Rule(when=(Pred("is_multi", "truthy"),), action="multi"),
    Rule(when=(), action="single"),
)


def evaluate_mcq_grade_kind(*, is_multi: bool) -> str:
    """Single-index vs multi-select MCQ measurement shape."""
    return first_match(_MCQ_GRADE_KIND_RULES, {"is_multi": is_multi}).action


def evaluate_mcq_correct(
    *,
    is_multi: bool,
    choice_index: int,
    correct_index: int,
    choice_indices: Sequence[int] | None = None,
    correct_indices: Sequence[int] | None = None,
) -> bool:
    """Instant MCQ correctness: set equality for multi-select, index match otherwise."""
    return bool(
        apply(
            evaluate_mcq_grade_kind(is_multi=is_multi),
            {
                "multi": lambda: set(choice_indices or []) == set(correct_indices or []),
                "single": lambda: int(choice_index) == correct_index,
            },
        )
    )

