"""Shared MCQ quiz service — create, take, score, review. Policy: qb.quiz_share.v1."""

from __future__ import annotations

import json
import secrets as pysecrets
import uuid
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, choose, first_match, pick
from app.services.llm_router import acomplete_chat

QUIZ_SHARE_POLICY = "quiz_share_v1"
QUIZ_SHARE_POLICY_VERSION = "qb.quiz_share.v1"

_INSERT_SET = text("""
    INSERT INTO mcq_quiz_sets (id, creator_name, title, description, share_slug)
    VALUES (CAST(:id AS uuid), :creator_name, :title, :desc, :slug)
""")

_INSERT_QUESTION = text("""
    INSERT INTO mcq_quiz_questions
        (quiz_set_id, question_text, options, correct_index, explanation, sort_order)
    VALUES (CAST(:qsid AS uuid), :qt, CAST(:opts AS jsonb), :ci, :exp, :so)
""")

_SET_COLUMNS = "id, creator_name, title, description, share_slug, created_at"
_QUESTION_COLUMNS = "id, question_text, options, sort_order"
_QUESTION_COLUMNS_WITH_ANSWERS = "id, question_text, options, explanation, sort_order"


def _slug() -> str:
    # 16-byte token -> 22 base64url chars; at most 2 are separators, so the
    # slice below always yields exactly 10 lowercase alphanumerics.
    raw = pysecrets.token_urlsafe(16).lower().replace("-", "").replace("_", "")
    return raw[:10]


# ── Rule table: question shape validity ───────────────────────────────────────

_QUESTION_RULES = (
    Rule(when=(Pred("is_dict", "falsey"),), action="reject"),
    Rule(when=(Pred("has_text", "falsey"),), action="reject"),
    Rule(when=(Pred("options_ok", "falsey"),), action="reject"),
    Rule(when=(Pred("index_ok", "falsey"),), action="reject"),
    Rule(when=(), action="accept"),
)


def _question_signals(q: Any) -> dict[str, Any]:
    is_dict = isinstance(q, dict)
    opts = pick(is_dict, lambda: q.get("options", []), lambda: [])
    index = pick(is_dict, lambda: q.get("correct_index", -1), lambda: -1)
    return {
        "is_dict": is_dict,
        "has_text": is_dict and "question_text" in q,
        "options_ok": isinstance(opts, list) and len(opts) == 4,
        "index_ok": isinstance(index, int) and 0 <= index < 4,
    }


def evaluate_question(q: Any) -> str:
    return first_match(_QUESTION_RULES, _question_signals(q)).action


def _shape(q: dict) -> dict:
    return {
        "question_text": q["question_text"],
        "options": q["options"],
        "correct_index": q["correct_index"],
        "explanation": q.get("explanation", ""),
    }


# ── Create ────────────────────────────────────────────────────────────────────

def _question_params(qs_id: str, sort_order: int, q: dict) -> dict:
    return {
        "qsid": qs_id,
        "qt": q["question_text"],
        "opts": json.dumps(q["options"]),
        "ci": q["correct_index"],
        "exp": q.get("explanation", ""),
        "so": sort_order,
    }


def create_quiz_set(
    db: Session,
    *,
    title: str,
    description: str,
    creator_name: str,
    questions: list[dict],
) -> dict:
    accepted = [_shape(q) for q in filter(lambda q: evaluate_question(q) == "accept", questions)]
    qs_id = str(uuid.uuid4())
    slug = _slug()
    db.execute(
        _INSERT_SET,
        {"id": qs_id, "creator_name": creator_name, "title": title, "desc": description, "slug": slug},
    )
    for sort_order, q in enumerate(accepted):
        db.execute(_INSERT_QUESTION, _question_params(qs_id, sort_order, q))
    db.commit()
    return {
        "id": qs_id,
        "slug": slug,
        "title": title,
        "question_count": len(accepted),
        "policy": QUIZ_SHARE_POLICY,
    }


# ── Read ──────────────────────────────────────────────────────────────────────

def _parse_options(q: Mapping) -> dict:
    return {**q, "options": pick(
        isinstance(q["options"], str),
        lambda: json.loads(q["options"]),
        lambda: q["options"],
    )}


def _load_set(db: Session, row: Mapping, include_answers: bool) -> dict:
    qs_id = str(row["id"])
    columns = choose(include_answers, _QUESTION_COLUMNS_WITH_ANSWERS, _QUESTION_COLUMNS)
    questions = [
        _parse_options(q)
        for q in db.execute(
            text(f"SELECT {columns} FROM mcq_quiz_questions WHERE quiz_set_id = CAST(:qsid AS uuid) ORDER BY sort_order"),
            {"qsid": qs_id},
        ).mappings().all()
    ]
    attempts = [
        dict(a)
        for a in db.execute(
            text("""
                SELECT taker_name, score, total_questions, completed_at
                FROM mcq_quiz_attempts WHERE quiz_set_id = CAST(:qsid AS uuid)
                AND completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 50
            """),
            {"qsid": qs_id},
        ).mappings().all()
    ]
    return {
        "id": qs_id,
        "title": row["title"],
        "description": row["description"],
        "creator": row["creator_name"],
        "slug": row["share_slug"],
        "created_at": str(row["created_at"]),
        "questions": questions,
        "attempts": choose(include_answers, attempts, []),
        "policy": QUIZ_SHARE_POLICY,
    }


def get_quiz_set(db: Session, slug: str, *, include_answers: bool = False) -> dict | None:
    row = db.execute(
        text(f"SELECT {_SET_COLUMNS} FROM mcq_quiz_sets WHERE share_slug = :slug"),
        {"slug": slug},
    ).mappings().first()
    return pick(row is not None, lambda: _load_set(db, row, include_answers), lambda: None)


# ── Submit attempt ────────────────────────────────────────────────────────────

def _grade(questions: Sequence, answers: list[int]) -> tuple[list[dict], int]:
    graded = [
        {
            "question_id": str(q["id"]),
            # slice + next keeps out-of-range answers as -1 without branching
            "selected": next(iter(answers[index : index + 1]), -1),
            "correct_index": q["correct_index"],
        }
        for index, q in enumerate(questions)
    ]
    verdicts = [
        {**row, "is_correct": row["selected"] == row["correct_index"]} for row in graded
    ]
    score = sum(choose(row["is_correct"], 1, 0) for row in verdicts)
    return verdicts, score


def submit_attempt(db: Session, slug: str, *, taker_name: str, answers: list[int]) -> dict | None:
    row = db.execute(
        text("SELECT id FROM mcq_quiz_sets WHERE share_slug = :slug"),
        {"slug": slug},
    ).mappings().first()

    def _record(qs_row: Mapping) -> dict:
        qs_id = str(qs_row["id"])
        questions = db.execute(
            text("SELECT id, correct_index FROM mcq_quiz_questions WHERE quiz_set_id = CAST(:qsid AS uuid) ORDER BY sort_order"),
            {"qsid": qs_id},
        ).mappings().all()
        verdicts, score = _grade(questions, answers)
        db.execute(
            text("""
                INSERT INTO mcq_quiz_attempts (quiz_set_id, taker_name, score, total_questions, answers, completed_at)
                VALUES (CAST(:qsid AS uuid), :name, :score, :total, CAST(:ans AS jsonb), now())
            """),
            {
                "qsid": qs_id,
                "name": taker_name,
                "score": score,
                "total": len(questions),
                "ans": json.dumps(verdicts),
            },
        )
        db.commit()
        return {"score": score, "total": len(questions), "results": verdicts, "policy": QUIZ_SHARE_POLICY}

    return pick(row is not None, lambda: _record(row), lambda: None)


# ── Creator results ───────────────────────────────────────────────────────────

def get_creator_results(db: Session, slug: str) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT a.taker_name, a.score, a.total_questions, a.completed_at, a.answers
            FROM mcq_quiz_attempts a
            JOIN mcq_quiz_sets qs ON qs.id = a.quiz_set_id
            WHERE qs.share_slug = :slug AND a.completed_at IS NOT NULL
            ORDER BY a.completed_at DESC
        """),
        {"slug": slug},
    ).mappings().all()
    return [dict(r) for r in rows]


# ── LLM generation ────────────────────────────────────────────────────────────

_GENERATION_PROMPT = (
    'Generate {count} multiple-choice questions about "{topic}" at {difficulty} difficulty.\n'
    "Return ONLY a valid JSON array, no markdown fences, no extra text. Each element:\n"
    '{{"question_text": "...", "options": ["A","B","C","D"], '
    '"correct_index": 0, "explanation": "..."}}\n'
    "Options must have exactly 4 items. correct_index is 0-3."
)

_FIX_PROMPT = (
    "Fix grammar, clarity, and formatting of these multiple-choice questions.\n"
    "Return ONLY the corrected JSON array with the same structure.\n"
    "Keep the correct_index the same. Fix spelling, grammar, and unclear wording.\n\n"
    "Questions:\n{payload}"
)


def _parse_json_array(raw: str) -> list[dict]:
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    parsed = json.loads(clean)
    return [q for q in filter(lambda q: evaluate_question(q) == "accept", pick(
        isinstance(parsed, list),
        lambda: parsed,
        lambda: [],
    ))]


async def generate_questions_llm(
    db: Session, *, topic: str, count: int = 5, difficulty: str = "medium"
) -> list[dict]:
    """Generate MCQ questions via the LLM; returns shape-validated question dicts."""
    prompt = _GENERATION_PROMPT.format(count=count, topic=topic, difficulty=difficulty)
    raw = await acomplete_chat(
        [{"role": "user", "content": prompt}], db, log_tag="quiz_llm_gen", strip_output=False
    )
    return _parse_json_array(raw)


async def fix_questions_llm(db: Session, questions: list[dict]) -> list[dict]:
    """Use the LLM to fix grammar, clarity, and formatting of existing questions."""
    prompt = _FIX_PROMPT.format(payload=json.dumps(questions, indent=2))
    raw = await acomplete_chat(
        [{"role": "user", "content": prompt}], db, log_tag="quiz_llm_fix", strip_output=False
    )
    return _parse_json_array(raw)
