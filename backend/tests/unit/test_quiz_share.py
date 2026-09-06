"""Quiz share service — rule table, grading, flows. Policy: qb.quiz_share.v1."""

from __future__ import annotations

import json

import pytest

from app.services import quiz_share

GOOD_Q = {"question_text": "What is 2+2?", "options": ["3", "4", "5", "6"], "correct_index": 1, "explanation": "basic"}


class FakeResult:
    def __init__(self, mappings_rows: list[dict]) -> None:
        self._rows = mappings_rows

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> dict | None:
        return next(iter(self._rows), None)

    def all(self) -> list[dict]:
        return self._rows


class FakeDb:
    """Records execute calls; serves scripted results in order."""

    def __init__(self, results: list[list[dict]] | None = None) -> None:
        self.calls: list[tuple] = []
        self._results = list(results or [])
        self._cursor = 0
        self.committed = 0

    def execute(self, clause, params=None) -> FakeResult:
        self.calls.append((str(clause).strip(), params))
        rows = next(iter(self._results[self._cursor :]), [])
        self._cursor += 1
        return FakeResult(rows)

    def commit(self) -> None:
        self.committed += 1


# ── Rule table ────────────────────────────────────────────────────────────────

def test_evaluate_question_accepts_valid() -> None:
    assert quiz_share.evaluate_question(GOOD_Q) == "accept"


def test_evaluate_question_rejects_wrong_option_count() -> None:
    assert quiz_share.evaluate_question({**GOOD_Q, "options": ["a", "b"]}) == "reject"


def test_evaluate_question_rejects_out_of_range_index() -> None:
    assert quiz_share.evaluate_question({**GOOD_Q, "correct_index": 9}) == "reject"
    assert quiz_share.evaluate_question({**GOOD_Q, "correct_index": -1}) == "reject"


def test_evaluate_question_rejects_non_dict_and_missing_text() -> None:
    assert quiz_share.evaluate_question("junk") == "reject"
    assert quiz_share.evaluate_question({"options": ["a", "b", "c", "d"]}) == "reject"


# ── Grading ───────────────────────────────────────────────────────────────────

QUESTIONS = [
    {"id": "q1", "correct_index": 0},
    {"id": "q2", "correct_index": 2},
    {"id": "q3", "correct_index": 1},
]


def test_grade_scores_correct_answers() -> None:
    verdicts, score = quiz_share._grade(QUESTIONS, [0, 2, 1])
    assert score == 3
    assert all(v["is_correct"] for v in verdicts)


def test_grade_flags_wrong_answers() -> None:
    verdicts, score = quiz_share._grade(QUESTIONS, [1, 0, 3])
    assert score == 0
    assert [v["selected"] for v in verdicts] == [1, 0, 3]


def test_grade_treats_missing_answers_as_unanswered() -> None:
    verdicts, score = quiz_share._grade(QUESTIONS, [0])
    assert score == 1
    assert verdicts[1]["selected"] == -1
    assert verdicts[1]["is_correct"] is False


def test_grade_handles_empty_answers() -> None:
    verdicts, score = quiz_share._grade(QUESTIONS, [])
    assert score == 0
    assert all(v["selected"] == -1 for v in verdicts)


# ── Slug ──────────────────────────────────────────────────────────────────────

def test_slug_is_ten_lowercase_urlsafe_chars() -> None:
    slugs = [quiz_share._slug() for _ in range(50)]
    assert all(len(slug) == 10 for slug in slugs)
    assert all(slug.isalnum() and slug == slug.lower() for slug in slugs)


# ── Create ────────────────────────────────────────────────────────────────────

def test_create_quiz_set_inserts_set_and_questions() -> None:
    db = FakeDb()
    result = quiz_share.create_quiz_set(
        db, title="Algebra", description="basics", creator_name="Benito",
        questions=[GOOD_Q, {**GOOD_Q, "question_text": "2+3?"}],
    )
    assert result["question_count"] == 2
    assert result["title"] == "Algebra"
    assert len(result["slug"]) == 10
    assert db.committed == 1
    inserts = list(filter(lambda c: "INSERT INTO mcq_quiz" in c[0], db.calls))
    assert len(inserts) == 3  # 1 set + 2 questions
    assert json.loads(inserts[1][1]["opts"]) == GOOD_Q["options"]


def test_create_quiz_set_filters_invalid_questions() -> None:
    db = FakeDb()
    result = quiz_share.create_quiz_set(
        db, title="t", description="", creator_name="x",
        questions=[GOOD_Q, {"question_text": "broken", "options": ["a"]}],
    )
    assert result["question_count"] == 1


# ── Read ──────────────────────────────────────────────────────────────────────

SET_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "creator_name": "Benito", "title": "Algebra", "description": "d",
    "share_slug": "abcd1234xy", "created_at": "2026-09-05T00:00:00Z",
}
Q_ROWS = [
    {"id": "q1", "question_text": "t1", "options": '["a","b","c","d"]', "sort_order": 0},
    {"id": "q2", "question_text": "t2", "options": ["1", "2", "3", "4"], "sort_order": 1},
]
ATTEMPT_ROWS = [{"taker_name": "Ana", "score": 2, "total_questions": 2, "completed_at": "2026-09-05"}]


def test_get_quiz_set_returns_none_when_missing() -> None:
    assert quiz_share.get_quiz_set(FakeDb([[]]), "nope") is None


def test_get_quiz_set_hides_answers_and_attempts_by_default() -> None:
    db = FakeDb([[SET_ROW], Q_ROWS, ATTEMPT_ROWS])
    result = quiz_share.get_quiz_set(db, "abcd1234xy")
    assert result["title"] == "Algebra"
    assert result["questions"][0]["options"] == ["a", "b", "c", "d"]  # json string parsed
    assert result["questions"][1]["options"] == ["1", "2", "3", "4"]  # list passthrough
    assert "explanation" not in result["questions"][0]
    assert result["attempts"] == []


def test_get_quiz_set_with_answers_includes_both() -> None:
    db = FakeDb([[SET_ROW], [{**Q_ROWS[0], "explanation": "because"}], ATTEMPT_ROWS])
    result = quiz_share.get_quiz_set(db, "abcd1234xy", include_answers=True)
    assert result["questions"][0]["explanation"] == "because"
    assert result["attempts"] == ATTEMPT_ROWS


# ── Submit ────────────────────────────────────────────────────────────────────

def test_submit_attempt_returns_none_when_quiz_missing() -> None:
    assert quiz_share.submit_attempt(FakeDb([[]]), "nope", taker_name="x", answers=[0]) is None


def test_submit_attempt_scores_and_persists() -> None:
    db = FakeDb([[{"id": SET_ROW["id"]}], [{"id": "q1", "correct_index": 0}, {"id": "q2", "correct_index": 1}]])
    result = quiz_share.submit_attempt(db, "abcd1234xy", taker_name="Ana", answers=[0, 9])
    assert result["score"] == 1
    assert result["total"] == 2
    assert result["results"][1]["is_correct"] is False
    assert db.committed == 1
    insert_call = db.calls[-1]
    assert "INSERT INTO mcq_quiz_attempts" in insert_call[0]
    assert json.loads(insert_call[1]["ans"])[0]["is_correct"] is True


# ── Creator results ───────────────────────────────────────────────────────────

def test_get_creator_results_returns_rows() -> None:
    db = FakeDb([[{"taker_name": "Ana", "score": 1, "total_questions": 2}]])
    rows = quiz_share.get_creator_results(db, "abcd1234xy")
    assert rows[0]["taker_name"] == "Ana"
    assert "JOIN mcq_quiz_sets" in db.calls[0][0]


# ── LLM JSON parsing ──────────────────────────────────────────────────────────

def test_parse_json_array_accepts_plain_json() -> None:
    parsed = quiz_share._parse_json_array(json.dumps([GOOD_Q]))
    assert parsed == [GOOD_Q]


def test_parse_json_array_strips_markdown_fences() -> None:
    raw = "```json\n" + json.dumps([GOOD_Q]) + "\n```"
    assert quiz_share._parse_json_array(raw) == [GOOD_Q]


def test_parse_json_array_drops_invalid_shapes() -> None:
    raw = json.dumps([GOOD_Q, {"oops": True}, {**GOOD_Q, "options": ["a"]}])
    assert quiz_share._parse_json_array(raw) == [GOOD_Q]


def test_parse_json_array_returns_empty_for_non_list() -> None:
    assert quiz_share._parse_json_array('{"question_text": "x"}') == []


# ── LLM generation ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_generate_questions_llm_parses_llm_output() -> None:
    async def fake_completion(messages, db, **kwargs) -> str:
        return json.dumps([GOOD_Q])

    original = quiz_share.acomplete_chat
    quiz_share.acomplete_chat = fake_completion
    result = await quiz_share.generate_questions_llm(FakeDb(), topic="algebra", count=1)
    quiz_share.acomplete_chat = original
    assert result == [GOOD_Q]


@pytest.mark.anyio
async def test_fix_questions_llm_returns_fixed_questions() -> None:
    fixed_q = {**GOOD_Q, "question_text": "What is two plus two?"}

    async def fake_completion(messages, db, **kwargs) -> str:
        return json.dumps([fixed_q])

    original = quiz_share.acomplete_chat
    quiz_share.acomplete_chat = fake_completion
    result = await quiz_share.fix_questions_llm(FakeDb(), questions=[GOOD_Q])
    quiz_share.acomplete_chat = original
    assert result == [fixed_q]


# ── API guards ────────────────────────────────────────────────────────────────

def test_present_raises_404_for_missing_quiz() -> None:
    from fastapi import HTTPException

    from study_api.quiz import _present

    with pytest.raises(HTTPException) as exc_info:
        _present(None)
    assert exc_info.value.status_code == 404


def test_present_passes_through_found_quiz() -> None:
    from study_api.quiz import _present

    assert _present({"title": "Algebra"}) == {"title": "Algebra"}
