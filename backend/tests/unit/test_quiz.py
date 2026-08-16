"""Unit tests for the Question Generator parsing/validation (pure, no DB/network)."""

from app.graphs.quiz_graph import (
    _finalize,
    _parse_questions,
    quiz_config_signature,
)
from app.services.session_design import QUIZ_MAX_QUESTIONS


def test_config_signature_normalizes():
    assert quiz_config_signature(["short", "mcq", "mcq"], 10, "Medium") == "mcq,short|10|medium"
    assert quiz_config_signature([], 999, "") == "mcq|40|mixed"  # caps + defaults
    assert quiz_config_signature(["bogus", "mcq"], 0, "easy") == "mcq|10|easy"  # drops unknown; 0→default 10


def test_parse_questions_salvages_truncation():
    raw = '[{"type":"mcq","prompt":"Q","options":["a","b"],"answer_index":0}, {"type":"truefalse","prompt":"T","answer":'
    got = _parse_questions(raw)
    assert len(got) == 1 and got[0]["type"] == "mcq"


def test_finalize_validates_each_type():
    items = [
        {"type": "mcq", "prompt": "Q1", "options": ["a", "b", "c"], "answer_index": 1, "explanation": "x"},
        {"type": "mcq", "prompt": "bad idx", "options": ["a", "b"], "answer_index": 5},  # out of range → drop
        {"type": "multi", "prompt": "Q2", "options": ["a", "b", "c"], "answer_indices": [0, 2]},
        {"type": "truefalse", "prompt": "Q3", "answer": "true"},  # string coerced to bool
        {"type": "fill_blank", "prompt": "_ blank", "answer": "x"},
        {"type": "short", "prompt": "Q5", "answer": "ans"},
        {"type": "essay", "prompt": "Q6", "answer": "outline"},
        {"type": "matching", "prompt": "Q7", "pairs": [{"left": "a", "right": "1"}, {"left": "b", "right": "2"}]},
        {"type": "matching", "prompt": "too few", "pairs": [{"left": "a", "right": "1"}]},  # <2 → drop
        {"type": "bogus", "prompt": "Q8"},  # unknown type → drop
        {"type": "mcq", "prompt": ""},  # empty prompt → drop
    ]
    out = _finalize(items, list(__import__("app.graphs.quiz_graph", fromlist=["QUESTION_TYPES"]).QUESTION_TYPES), 40)
    types = [q["type"] for q in out]
    assert types == ["mcq", "multi", "truefalse", "fill_blank", "short", "essay", "matching"]
    assert out[2]["answer"] is True  # "true" → bool


def test_finalize_validates_mcq_variant_types():
    """The new MCQ-style variants are single-best-answer: same payload as mcq."""
    items = [
        {"type": "mcq_negative", "prompt": "Which is NOT a primary color?", "options": ["Red", "Blue", "Green", "Yellow"], "answer_index": 2, "explanation": "x"},
        {"type": "assertion_reason", "prompt": "A: water boils at 100C. R: at sea level pressure.", "options": ["both true, R explains A", "both true, R does not explain A", "A true R false", "A false R true"], "answer_index": 0},
        {"type": "scenario", "prompt": "A car skids on ice. Why?", "options": ["low friction", "high friction", "more mass", "downhill"], "answer_index": 0},
        {"type": "cloze", "prompt": "The powerhouse of the cell is the ___.", "options": ["nucleus", "mitochondrion", "ribosome", "vacuole"], "answer_index": 1},
        # malformed: out-of-range answer_index → drop
        {"type": "scenario", "prompt": "bad idx", "options": ["a", "b"], "answer_index": 9},
        # malformed: too few options → drop
        {"type": "cloze", "prompt": "only one ___", "options": ["x"], "answer_index": 0},
        # malformed: missing answer_index → drop
        {"type": "assertion_reason", "prompt": "no answer", "options": ["a", "b", "c", "d"]},
    ]
    all_types = list(__import__("app.graphs.quiz_graph", fromlist=["QUESTION_TYPES"]).QUESTION_TYPES)
    out = _finalize(items, all_types, 40)
    types = [q["type"] for q in out]
    assert types == ["mcq_negative", "assertion_reason", "scenario", "cloze"]
    assert all("options" in q and "answer_index" in q for q in out)
    assert out[0]["answer_index"] == 2  # negative/EXCEPT pick preserved


def test_finalize_respects_requested_types_and_cap():
    items = [{"type": "mcq", "prompt": f"Q{i}", "options": ["a", "b"], "answer_index": 0} for i in range(50)]
    items.append({"type": "essay", "prompt": "essay", "answer": "x"})
    out = _finalize(items, ["mcq"], 5)  # only mcq requested, cap 5
    assert len(out) == 5 and all(q["type"] == "mcq" for q in out)
    assert QUIZ_MAX_QUESTIONS == 40


def test_parse_empty():
    assert _parse_questions("") == []
    assert _parse_questions("not json") == []
