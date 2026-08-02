"""Coverage for migration_sql + quiz_export (previously 0%)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# --- migration_sql ----------------------------------------------------------


def test_execute_sql_file_runs_via_op(monkeypatch, tmp_path) -> None:
    from app import migration_sql

    sql_file = tmp_path / "test.sql"
    sql_file.write_text("CREATE TABLE x (id int); -- 50% done")
    monkeypatch.setattr(migration_sql, "SCHEMA_DIR", tmp_path)

    bind = MagicMock()
    ctx = MagicMock()
    ctx.autocommit_block.return_value.__enter__.return_value = ctx

    with patch("alembic.op.get_context", return_value=ctx), patch(
        "alembic.op.get_bind", return_value=bind
    ):
        migration_sql.execute_sql_file("test.sql")

    # psycopg3 % → %% escaping happened.
    bind.exec_driver_sql.assert_called_once()
    sql = bind.exec_driver_sql.call_args[0][0]
    assert "50%% done" in sql
    assert "%" not in sql.replace("%%", "")


def test_execute_sql_file_missing_raises(monkeypatch, tmp_path) -> None:
    from app import migration_sql

    monkeypatch.setattr(migration_sql, "SCHEMA_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        migration_sql.execute_sql_file("nope.sql")


# --- quiz_export ------------------------------------------------------------


def test_build_quiz_docx_all_types() -> None:
    from app.services.quiz_export import build_quiz_docx

    questions = [
        {"type": "mcq", "prompt": "Q1?", "options": ["a", "b"], "answer_index": 1, "explanation": "because"},
        {"type": "multi", "prompt": "Q2?", "options": ["a", "b", "c"], "answer_indices": [0, 2], "explanation": ""},
        {"type": "truefalse", "prompt": "Q3?", "answer": True},
        {"type": "fill_blank", "prompt": "Q4 ___", "answer": "x"},
        {"type": "short", "prompt": "Q5?", "answer": "y"},
        {"type": "essay", "prompt": "Q6?"},
        {"type": "matching", "prompt": "Q7?", "pairs": [{"left": "L1", "right": "R1"}]},
        {"type": "unknown", "prompt": "Q8?"},
    ]
    data = build_quiz_docx(title="Test quiz", questions=questions, with_answers=True)
    assert data.startswith(b"PK")  # docx is a zip
    assert len(data) > 1000

    data_no_answers = build_quiz_docx(title="Test quiz", questions=questions, with_answers=False)
    assert data_no_answers.startswith(b"PK")


def test_answer_text_branches() -> None:
    from app.services.quiz_export import _answer_text

    assert _answer_text({"type": "mcq", "options": ["a", "b"], "answer_index": 1}) == "B. b"
    assert _answer_text({"type": "mcq", "options": ["a"], "answer_index": 5}) == "F"
    assert (
        _answer_text({"type": "multi", "options": ["a", "b", "c"], "answer_indices": [0, 2]})
        == "A. a, C. c"
    )
    assert _answer_text({"type": "truefalse", "answer": True}) == "True"
    assert _answer_text({"type": "truefalse", "answer": False}) == "False"
    assert _answer_text({"type": "short", "answer": "hello"}) == "hello"
    assert (
        _answer_text({"type": "matching", "pairs": [{"left": "L", "right": "R"}]})
        == "L → R"
    )
    assert _answer_text({"type": "nonsense"}) == ""


# --- resume_export ----------------------------------------------------------


def test_build_resume_docx_templates() -> None:
    from app.services.resume_export import build_resume_docx

    sample = {
        "name": "Jane Doe",
        "title": "Backend Engineer",
        "email": "jane@example.com",
        "phone": "+1 415 555 1234",
        "location": "SF",
        "links": ["github.com/jane"],
        "summary": "Backend engineer with 5 years building scalable services.",
        "experience": [
            {
                "role": "Senior Engineer",
                "company": "Acme",
                "dates": "2021-now",
                "bullets": ["Led latency cut of 40%", "Shipped 3 services"],
            }
        ],
        "education": [{"degree": "BS CS", "school": "MIT", "dates": "2016"}],
        "skills": ["Python", "Go", "SQL"],
    }
    for tmpl in ("ats", "modern"):
        out = build_resume_docx(sample, template=tmpl)
        assert out.startswith(b"PK")
        assert len(out) > 1000


def test_build_resume_docx_minimal() -> None:
    from app.services.resume_export import build_resume_docx

    out = build_resume_docx({}, template="ats")
    assert out.startswith(b"PK")
    assert len(out) > 500

