"""Interview Mode — a live, multi-round mock interview driven by the resume.

Additive 8th study mode. The learner uploads their resume (a normal source), picks a
target company category, and we run a round-by-round interview: one question at a time
(MCQ or typed), evaluating each answer before asking the next. Unlike the cached study
artifacts (notes/quiz/palace), the single ``qb.document_interview`` row is MUTATED per
turn — it holds the resolved round plan, a cursor, and the growing transcript.

Reuses: the resume chunks already ingested (``DocumentChunk``), the shared LLM primitive
(``complete_chat``), and the MCQ grader (``grade_mcq_answer``) for MCQ turns. Typed rounds
(technical / system-design / behavioural) are scored on a 1-4 rubric by the LLM.

# ponytail: fixed question count per round, transcript-aware so the model can probe
# naturally; add dynamic follow-up injection later only if the fixed count feels shallow.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import apply, pick
from app.models import Document, DocumentChunk
from app.services.document_learner_state import learner_key_for_user
from app.services.llm_json import extract_json_obj
from app.services.llm_router import complete_chat
from app.services.open_response import (
    INTERVIEW_DIMENSIONS as RUBRIC_DIMENSIONS,
    build_interview_report,
    clamp_interview_score as _clamp_score,
    degraded_interview_scores,
    empty_interview_scores,
    evaluate_interview_coding_turn,
    evaluate_interview_turn_kind,
    plan_resume_chunk_limit,
    plan_resume_input_tokens,
    resolve_coding_language_id,
    shape_interview_scores,
)
from app.services.session_design import plan_interview_rounds
from app.services.token_budget import truncate_to_tokens

# Round plans live in Session Design (`plan_interview_rounds`).
Round = dict[str, Any]

# value -> {label, blurb} for the setup picker (single source of truth; frontend renders these).
CATEGORY_META: dict[str, dict[str, str]] = {
    "service": {"label": "Service-based", "blurb": "TCS, Infosys, Wipro, Accenture, Cognizant"},
    "product": {"label": "Product-based", "blurb": "FAANG, Flipkart, PhonePe, Swiggy, CRED"},
    "bank": {"label": "Bank / Fintech", "blurb": "Goldman, JPMorgan, Razorpay, Paytm"},
    "startup": {"label": "Startup", "blurb": "Early-stage, fast-paced, generalist"},
    "other": {"label": "Other", "blurb": "A balanced general interview"},
}

# Built-in, self-contained stdin→stdout problems used when the LLM can't produce a gradable
# coding question — so the DSA round is always a real coding round, never a silent downgrade.
_FALLBACK_CODING: list[dict[str, Any]] = [
    {
        "question": "Read a line of space-separated integers from stdin and print their sum.",
        "starter_code": "nums = list(map(int, input().split()))\nprint(sum(nums))",
        "explanation": "Parse the ints and sum them.",
        "tests": [
            {"stdin": "1 2 3 4", "expected_output": "10"},
            {"stdin": "-5 5", "expected_output": "0"},
            {"stdin": "42", "expected_output": "42"},
        ],
    },
    {
        "question": "Read a string from stdin and print it reversed.",
        "starter_code": "s = input()\nprint(s[::-1])",
        "explanation": "Slice with a -1 step.",
        "tests": [
            {"stdin": "hello", "expected_output": "olleh"},
            {"stdin": "racecar", "expected_output": "racecar"},
            {"stdin": "ab", "expected_output": "ba"},
        ],
    },
    {
        "question": "Read an integer n, then a line of n space-separated integers. Print the maximum.",
        "starter_code": "n = int(input())\nnums = list(map(int, input().split()))\nprint(max(nums))",
        "explanation": "Read n (unused for the max) then the list; print max.",
        "tests": [
            {"stdin": "3\n4 9 2", "expected_output": "9"},
            {"stdin": "1\n-7", "expected_output": "-7"},
            {"stdin": "4\n5 5 5 5", "expected_output": "5"},
        ],
    },
]

_GEN_SYSTEM = (
    "You are a senior technical interviewer running a live mock interview for a candidate, "
    "grounded in their resume. Ask ONE realistic, high-signal question for the current round. "
    "Tailor it to the candidate's actual experience, projects and skills where relevant; do not "
    "repeat any question already asked. Keep the question focused and answerable in a few minutes. "
    "Reply with STRICT JSON only, no markdown, no preamble."
)

_EVAL_SYSTEM = (
    "You are evaluating a candidate's answer in a mock interview. Be fair but calibrated to a real "
    "hiring bar. Score each dimension from 1 to 4 (1=poor, 2=below bar, 3=meets bar, 4=exceeds bar): "
    "problem_framing, depth, tradeoffs, communication. Then write concise feedback (2-4 sentences): "
    "name what was strong, the single biggest gap, and one concrete way to improve. "
    "Reply with STRICT JSON only: "
    '{"scores":{"problem_framing":n,"depth":n,"tradeoffs":n,"communication":n},"feedback":"..."}'
)


def _raise_value(msg: str) -> None:
    raise ValueError(msg)


# ---------------------------------------------------------------- persistence
def resolve_interview_learner_key(
    user_id: uuid.UUID | None, guest_id: str | None
) -> str:
    return learner_key_for_user(user_id, guest_id) or "anonymous"


def load_interview(
    db: Session, document_id: uuid.UUID, *, learner_key: str
) -> dict[str, Any]:
    """Return the interview state for the client (answers stripped from the pending question)."""
    row = db.execute(
        text(
            "SELECT category, config, state, transcript, status, error "
            "FROM qb.document_interview WHERE document_id = :id AND learner_key = :key"
        ),
        {"id": document_id, "key": learner_key},
    ).mappings().first()
    return pick(
        not row,
        lambda: {"status": "missing", "categories": CATEGORY_META, **_empty()},
        lambda: _load_interview_row(row),
    )


def _load_interview_row(row: Any) -> dict[str, Any]:
    config = _as_json(row["config"], [])
    state = _as_json(row["state"], {})
    transcript = _as_json(row["transcript"], [])
    current = (state or {}).get("current")
    out = {
        "status": row["status"],
        "category": row["category"] or "",
        "categories": CATEGORY_META,
        "rounds": [{"name": r["name"], "kind": r["kind"], "questions": r["questions"]} for r in config],
        "round_index": (state or {}).get("round_index", 0),
        "total_rounds": len(config),
        "current_question": pick(
            bool(current),
            lambda: _public_question(current),
            lambda: None,
        ),
        "transcript": [_public_turn(t) for t in transcript],
        "error": row["error"],
    }
    pick(
        row["status"] == "complete",
        lambda: out.__setitem__("report", build_report(config, transcript)),
        lambda: None,
    )
    return out


def _empty() -> dict[str, Any]:
    return {
        "category": "", "rounds": [], "round_index": 0, "total_rounds": 0,
        "current_question": None, "transcript": [], "error": None,
    }


def _save(
    db: Session,
    document_id: uuid.UUID,
    *,
    learner_key: str,
    category: str,
    config: list,
    state: dict,
    transcript: list,
    status: str,
    error: str | None = None,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_interview
                (document_id, learner_key, category, config, state, transcript, status, error, updated_at)
            VALUES (:id, :key, :category, CAST(:config AS jsonb), CAST(:state AS jsonb),
                    CAST(:transcript AS jsonb), :status, :error, now())
            ON CONFLICT (document_id, learner_key) DO UPDATE SET
                category = EXCLUDED.category, config = EXCLUDED.config, state = EXCLUDED.state,
                transcript = EXCLUDED.transcript, status = EXCLUDED.status,
                error = EXCLUDED.error, updated_at = now()
            """
        ),
        {
            "id": document_id,
            "key": learner_key,
            "category": category,
            "config": json.dumps(config),
            "state": json.dumps(state),
            "transcript": json.dumps(transcript),
            "status": status,
            "error": error,
        },
    )
    db.commit()


def reset_interview(
    db: Session, document_id: uuid.UUID, *, learner_key: str
) -> dict[str, Any]:
    db.execute(
        text(
            "DELETE FROM qb.document_interview WHERE document_id = :id AND learner_key = :key"
        ),
        {"id": document_id, "key": learner_key},
    )
    db.commit()
    return load_interview(db, document_id, learner_key=learner_key)


# ---------------------------------------------------------------- orchestration
async def start_interview(
    db: Session, document_id: uuid.UUID, category: str, *, learner_key: str
) -> dict[str, Any]:
    """Begin (or resume) an interview for the chosen company category."""
    planned = plan_interview_rounds(category)
    category = planned.category
    existing = load_interview(db, document_id, learner_key=learner_key)
    return pick(
        existing["status"] == "in_progress" and existing["category"] == category,
        lambda: existing,
        lambda: None,
    ) or await _start_fresh(db, document_id, category, learner_key, planned)


async def _start_fresh(
    db: Session, document_id: uuid.UUID, category: str, learner_key: str, planned: Any
) -> dict[str, Any]:
    config = [dict(r) for r in planned.rounds]
    resume = _resume_text(db, document_id)
    first = await _generate_question(db, category, config[0], resume, asked=[])
    state = {"round_index": 0, "q_in_round": 0, "current": first}
    _save(
        db,
        document_id,
        learner_key=learner_key,
        category=category,
        config=config,
        state=state,
        transcript=[],
        status="in_progress",
    )
    return load_interview(db, document_id, learner_key=learner_key)


async def submit_answer(
    db: Session, document_id: uuid.UUID, answer: Any, *, learner_key: str
) -> dict[str, Any]:
    """Evaluate the answer to the current question, then advance to the next one."""
    row = db.execute(
        text(
            "SELECT category, config, state, transcript, status "
            "FROM qb.document_interview WHERE document_id = :id AND learner_key = :key"
        ),
        {"id": document_id, "key": learner_key},
    ).mappings().first()
    pick(
        not row or row["status"] != "in_progress",
        lambda: _raise_value("no interview in progress"),
        lambda: None,
    )
    category = row["category"]
    config = _as_json(row["config"], [])
    state = _as_json(row["state"], {})
    transcript = _as_json(row["transcript"], [])
    current = state.get("current")
    pick(not current, lambda: _raise_value("no pending question"), lambda: None)

    turn = await _evaluate(db, current, answer)
    transcript.append(turn)

    from app.services.session_design import plan_interview_advance

    advance = plan_interview_advance(
        round_index=int(state["round_index"]),
        q_in_round=int(state["q_in_round"]),
        round_question_count=int(config[int(state["round_index"])]["questions"]),
        round_count=len(config),
    )
    return pick(
        advance.complete,
        lambda: _finish_interview(
            db, document_id, learner_key, category, config, advance, transcript
        ),
        lambda: None,
    ) or await _continue_interview(
        db, document_id, learner_key, category, config, advance, transcript
    )


def _finish_interview(
    db: Session,
    document_id: uuid.UUID,
    learner_key: str,
    category: str,
    config: list,
    advance: Any,
    transcript: list,
) -> dict[str, Any]:
    _save(
        db,
        document_id,
        learner_key=learner_key,
        category=category,
        config=config,
        state={
            "round_index": advance.round_index,
            "q_in_round": advance.q_in_round,
            "current": None,
        },
        transcript=transcript,
        status="complete",
    )
    return load_interview(db, document_id, learner_key=learner_key)


async def _continue_interview(
    db: Session,
    document_id: uuid.UUID,
    learner_key: str,
    category: str,
    config: list,
    advance: Any,
    transcript: list,
) -> dict[str, Any]:
    ri, qi = advance.round_index, advance.q_in_round
    resume = _resume_text(db, document_id)
    asked = [t["question"] for t in transcript]
    nxt = await _generate_question(db, category, config[ri], resume, asked=asked)
    _save(
        db,
        document_id,
        learner_key=learner_key,
        category=category,
        config=config,
        state={"round_index": ri, "q_in_round": qi, "current": nxt},
        transcript=transcript,
        status="in_progress",
    )
    return load_interview(db, document_id, learner_key=learner_key)


# ---------------------------------------------------------------- LLM: generate
async def _generate_question(
    db: Session, category: str, rnd: Round, resume: str, *, asked: list[str]
) -> dict[str, Any]:
    label = CATEGORY_META.get(category, {}).get("label", category)
    from app.services.session_design import (
        plan_interview_asked_context,
        plan_interview_gen_contract,
    )

    already = "\n".join(f"- {q}" for q in asked[-plan_interview_asked_context():]) or "(none yet)"

    contract = plan_interview_gen_contract(str(rnd.get("kind") or ""))
    schema = contract.schema
    rules = contract.rules
    user = (
        f"CANDIDATE RESUME:\n{truncate_to_tokens(resume, plan_resume_input_tokens()) or '(resume unavailable)'}\n\n"
        f"Company type: {label}\n"
        f"Round: {rnd['name']} — focus on {rnd['focus']}.\n\n"
        f"Questions already asked this interview (do not repeat):\n{already}\n\n"
        f"Ask one {rnd['kind']} question. {rules}\nReturn JSON matching: {schema}"
    )
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    resume_digest = content_hash_key("resume", truncate_to_tokens(resume, plan_resume_input_tokens()))
    asked_digest = content_hash_key("asked", already)
    gen_key = content_hash_key(
        "interview_gen", category, rnd["name"], rnd["kind"], rnd["focus"], resume_digest, asked_digest
    )
    hit = cache_get(db, kind="interview_gen", cache_key=gen_key)
    return pick(
        isinstance(hit, dict) and bool(hit.get("question")),
        lambda: hit,
        lambda: None,
    ) or await _generate_question_fresh(db, rnd, user, asked, contract, gen_key, cache_put)


async def _generate_question_fresh(
    db: Session,
    rnd: Round,
    user: str,
    asked: list[str],
    contract: Any,
    gen_key: str,
    cache_put: Any,
) -> dict[str, Any]:
    raw = await complete_chat(
        [{"role": "system", "content": _GEN_SYSTEM}, {"role": "user", "content": user}],
        db, log_tag="interview_gen",
    )
    data = _parse_json_obj(raw)
    q = (data.get("question") or "").strip()
    out: dict[str, Any] = {
        "kind": rnd["kind"],
        "round_name": rnd["name"],
        "focus": rnd["focus"],
        "question": q or _fallback_question(rnd),
    }
    apply(
        evaluate_interview_turn_kind(str(rnd["kind"])),
        {
            "mcq": lambda: _fill_generated_mcq(out, data, contract),
            "coding": lambda: _fill_generated_coding(out, data, contract, asked),
            "typed": lambda: None,
        },
    )
    pick(
        bool(out.get("question")),
        lambda: cache_put(db, kind="interview_gen", cache_key=gen_key, value=out),
        lambda: None,
    )
    return out


def _fill_generated_mcq(out: dict[str, Any], data: dict[str, Any], contract: Any) -> None:
    from app.services.session_design import plan_interview_question_shape

    opts = list(filter(None, (str(o).strip() for o in (data.get("options") or []))))
    raw_ci = str(data.get("correct_index", "")).strip()
    ci = pick(raw_ci != "", lambda: int(data.get("correct_index", 0)), lambda: 0)
    mcq_valid = len(opts) >= 2 and 0 <= ci < len(opts)
    shape = plan_interview_question_shape(
        planned_kind="mcq", mcq_valid=mcq_valid, coding_has_tests=True
    )
    apply(
        shape,
        {
            "typed": lambda: out.__setitem__("kind", "typed"),
            "mcq": lambda: _set_mcq_fields(out, opts, ci, data, contract),
        },
    )


def _set_mcq_fields(
    out: dict[str, Any], opts: list[str], ci: int, data: dict[str, Any], contract: Any
) -> None:
    out["options"] = opts[: contract.max_options]
    out["correct_index"] = min(ci, len(out["options"]) - 1)
    out["explanation"] = (data.get("explanation") or "").strip()


def _fill_generated_coding(
    out: dict[str, Any], data: dict[str, Any], contract: Any, asked: list[str]
) -> None:
    from app.services.code_execution import DEFAULT_LANGUAGE_ID, LANGUAGES
    from app.services.session_design import (
        pick_interview_coding_fallback,
        plan_interview_question_shape,
    )

    tests = [
        {"stdin": str(t.get("stdin", "")), "expected_output": str(t.get("expected_output", ""))}
        for t in filter(
            lambda t: isinstance(t, dict) and str(t.get("expected_output", "")).strip() != "",
            data.get("tests") or [],
        )
    ]
    shape = plan_interview_question_shape(
        planned_kind="coding", mcq_valid=True, coding_has_tests=bool(tests)
    )
    apply(
        shape,
        {
            "coding_fallback": lambda: out.update(
                {
                    **pick_interview_coding_fallback(len(asked), _FALLBACK_CODING),
                    "kind": "coding",
                    "language_id": DEFAULT_LANGUAGE_ID,
                }
            ),
            "coding": lambda: _set_coding_fields(
                out, data, tests, contract, LANGUAGES, DEFAULT_LANGUAGE_ID
            ),
        },
    )


def _set_coding_fields(
    out: dict[str, Any],
    data: dict[str, Any],
    tests: list[dict[str, str]],
    contract: Any,
    languages: Any,
    default_language_id: int,
) -> None:
    lid = data.get("language_id", default_language_id)
    out["language_id"] = resolve_coding_language_id(
        lid, languages, default=default_language_id
    )
    out["starter_code"] = (data.get("starter_code") or "").rstrip()
    out["tests"] = tests[: contract.max_tests]
    out["explanation"] = (data.get("explanation") or "").strip()


def _fallback_question(rnd: Round) -> str:
    return f"Tell me about your experience relevant to {rnd['focus']}."


# ---------------------------------------------------------------- LLM: evaluate
async def _evaluate(db: Session, current: dict, answer: Any) -> dict[str, Any]:
    turn: dict[str, Any] = {
        "round_name": current.get("round_name", ""),
        "kind": current["kind"],
        "question": current.get("question", ""),
    }
    return await apply(
        evaluate_interview_turn_kind(str(current["kind"])),
        {
            "coding": lambda: _evaluate_coding(db, current, answer, turn),
            "mcq": lambda: _evaluate_mcq(db, current, answer, turn),
            "typed": lambda: _evaluate_typed(db, current, answer, turn),
        },
    )


async def _evaluate_coding(
    db: Session, current: dict, answer: Any, turn: dict[str, Any]
) -> dict[str, Any]:
    from app.services.code_execution import LANGUAGES, run_tests

    src = pick(
        isinstance(answer, dict),
        lambda: str((answer or {}).get("source", "")),
        lambda: str(answer or ""),
    )
    lang = pick(
        isinstance(answer, dict),
        lambda: (answer or {}).get("language_id"),
        lambda: None,
    )
    language_id = pick(
        lang in LANGUAGES,
        lambda: lang,
        lambda: current.get("language_id", 71),
    )
    tests = current.get("tests") or []
    res = await run_tests(src, language_id, tests)
    failed = next(filter(lambda c: not c["ok"], res.get("cases") or []), None)
    measured = evaluate_interview_coding_turn(
        source=src,
        language_id=language_id,
        passed=res["passed"],
        total=res["total"],
        error=res.get("error"),
        first_fail=failed,
    )
    turn.update(measured.result)
    return turn


async def _evaluate_mcq(
    db: Session, current: dict, answer: Any, turn: dict[str, Any]
) -> dict[str, Any]:
    from app.graphs.mcq_graph import grade_mcq_answer

    options = current.get("options") or []
    correct_index = int(current.get("correct_index", 0))
    try:
        selected = int(answer)
    except (TypeError, ValueError):
        selected = -1
    result = await grade_mcq_answer(
        db,
        question=current.get("question", ""),
        options=options,
        correct_index=correct_index,
        selected_index=selected,
        explanation=current.get("explanation", ""),
    )
    turn.update({
        "options": options,
        "selected_index": selected,
        "correct_index": correct_index,
        "correct": bool(result["is_correct"]),
        "feedback": result["feedback"],
    })
    return turn


async def _evaluate_typed(
    db: Session, current: dict, answer: Any, turn: dict[str, Any]
) -> dict[str, Any]:
    answer_text = str(answer or "").strip()
    turn["answer"] = answer_text
    return pick(
        not answer_text,
        lambda: _empty_typed_turn(turn),
        lambda: None,
    ) or await _score_typed(db, current, answer_text, turn)


def _empty_typed_turn(turn: dict[str, Any]) -> dict[str, Any]:
    empty = empty_interview_scores()
    turn.update(empty.result)
    return turn


async def _score_typed(
    db: Session, current: dict, answer_text: str, turn: dict[str, Any]
) -> dict[str, Any]:
    user = (
        f"Round: {current.get('round_name','')} - focus: {current.get('focus','')}\n"
        f"Question: {current.get('question','')}\n\n"
        f"Candidate's answer:\n{answer_text}"
    )
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    eval_key = content_hash_key(
        "interview_eval",
        current.get("question", ""),
        answer_text,
        current.get("round_name", ""),
        current.get("focus", ""),
    )
    hit = cache_get(db, kind="interview_eval", cache_key=eval_key)
    return pick(
        isinstance(hit, dict) and bool(hit.get("scores")),
        lambda: _typed_from_cache(turn, hit),
        lambda: None,
    ) or await _score_typed_llm(db, user, eval_key, cache_put, turn)


def _typed_from_cache(turn: dict[str, Any], hit: dict[str, Any]) -> dict[str, Any]:
    turn.update({"scores": hit["scores"], "feedback": hit.get("feedback") or "Answer recorded."})
    return turn


async def _score_typed_llm(
    db: Session, user: str, eval_key: str, cache_put: Any, turn: dict[str, Any]
) -> dict[str, Any]:
    try:
        raw = await complete_chat(
            [{"role": "system", "content": _EVAL_SYSTEM}, {"role": "user", "content": user}],
            db, log_tag="interview_eval",
        )
        data = _parse_json_obj(raw)
        scores = shape_interview_scores(
            pick(
                isinstance(data.get("scores"), dict),
                lambda: data.get("scores"),
                lambda: {},
            )
        ).result["scores"]
        feedback = (data.get("feedback") or "").strip() or "Answer recorded."
        cache_put(db, kind="interview_eval", cache_key=eval_key, value={"scores": scores, "feedback": feedback})
    except Exception:
        degraded = degraded_interview_scores()
        scores = degraded.result["scores"]
        feedback = degraded.result["feedback"]
    turn.update({"scores": scores, "feedback": feedback})
    return turn


def build_report(config: list, transcript: list) -> dict[str, Any]:
    """Per-round + overall score - Open Response Measurement Engine."""
    return build_interview_report(config, transcript).result


# ---------------------------------------------------------------- helpers
def _resume_text(db: Session, document_id: uuid.UUID) -> str:
    doc = db.get(Document, document_id)
    return pick(not doc, lambda: "", lambda: _resume_chunks(db, document_id))


def _resume_chunks(db: Session, document_id: uuid.UUID) -> str:
    rows = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.page_start.asc())
        .limit(plan_resume_chunk_limit())
        .all()
    )
    return "\n\n".join(filter(None, (r.text for r in rows)))


def _public_question(q: dict) -> dict[str, Any]:
    """Strip the answer key (correct_index / hidden tests / explanation) before sending
    a pending question to the client."""
    out = {"kind": q["kind"], "round_name": q.get("round_name", ""), "question": q.get("question", "")}
    apply(
        evaluate_interview_turn_kind(str(q["kind"])),
        {
            "mcq": lambda: out.__setitem__("options", q.get("options", [])),
            "coding": lambda: out.update(
                {
                    "starter_code": q.get("starter_code", ""),
                    "language_id": q.get("language_id", 71),
                    "test_count": len(q.get("tests") or []),
                }
            ),
            "typed": lambda: None,
        },
    )
    return out


def _public_turn(t: dict) -> dict[str, Any]:
    """Answered turns may safely reveal the key (for review)."""
    return t


def _as_json(v: Any, default: Any) -> Any:
    return pick(
        v is None,
        lambda: default,
        lambda: pick(isinstance(v, str), lambda: json.loads(v), lambda: v),
    )


# Tolerant LLM-JSON extraction lives in app.services.llm_json (shared with resume.py).
_parse_json_obj = extract_json_obj


def _advance_cursor(ri: int, qi: int, cfg: list[dict[str, Any]]) -> tuple[int, int]:
    qi += 1
    return pick(
        qi >= cfg[ri]["questions"],
        lambda: (ri + 1, 0),
        lambda: (ri, qi),
    )


def _self_check() -> None:
    # ponytail: one runnable check — no DB/LLM. Exercises the pure logic: cursor
    # advance through a plan, report math, and tolerant JSON parsing.
    cfg = [dict(r) for r in plan_interview_rounds("product").rounds]
    total_q = sum(r["questions"] for r in cfg)
    assert total_q == 7, total_q  # DSA round is now 1 coding question (was 3 MCQ)
    assert cfg[0]["kind"] == "coding", cfg[0]

    ri, qi, seen = 0, 0, 0
    while ri < len(cfg):
        seen += 1
        ri, qi = _advance_cursor(ri, qi, cfg)
    assert seen == total_q, (seen, total_q)

    transcript = [
        {"round_name": "DSA / Coding", "kind": "coding", "passed": 3, "total": 4},
        {"round_name": "Behavioural", "kind": "typed",
         "scores": {d: 4 for d in RUBRIC_DIMENSIONS}},
    ]
    rep = build_report(cfg, transcript)
    dsa = next(filter(lambda r: r["name"] == "DSA / Coding", rep["rounds"]))
    beh = next(filter(lambda r: r["name"] == "Behavioural", rep["rounds"]))
    assert dsa["score"] == 75, dsa
    assert beh["score"] == 100, beh
    assert _clamp_score(9) == 4 and _clamp_score(0) == 1 and _clamp_score("x") == 2

    for fb in _FALLBACK_CODING:
        assert fb["question"] and fb["starter_code"] and fb["tests"], fb
        assert all(t["expected_output"] for t in fb["tests"]), fb

    assert _parse_json_obj('```json\n{"question":"hi"}\n```')["question"] == "hi"
    assert _parse_json_obj("noise {\"a\":1} tail")["a"] == 1
    assert _parse_json_obj("not json") == {}
    print("interview self-check OK")


pick(__name__ == "__main__", _self_check, lambda: None)
