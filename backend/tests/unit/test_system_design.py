"""Unit tests for System Design mastery helpers."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services.system_design import (
    CONCEPTS,
    PROBLEMS,
    _heuristic_grade,
    _pick_next_problem_id,
    build_path,
    recommend_problem,
)


def test_concepts_form_a_spine() -> None:
    keys = [c["key"] for c in CONCEPTS]
    assert "requirements" in keys
    assert "caching" in keys
    assert len(CONCEPTS) >= 6
    # prerequisites only point at known keys
    for c in CONCEPTS:
        for p in c["prerequisites"]:
            assert p in keys


def test_problems_map_onto_concepts() -> None:
    keys = {c["key"] for c in CONCEPTS}
    assert len(PROBLEMS) >= 10
    for p in PROBLEMS:
        assert p["slug"]
        assert p["reference_design"]
        assert p["concept_keys"]
        assert set(p["concept_keys"]) <= keys


def test_heuristic_grade_returns_lesson_and_dims() -> None:
    graded = _heuristic_grade(
        {
            "requirements": "Users create short links and redirect.",
            "apis": "POST /links GET /{code}",
            "data": "KV store for mappings",
            "scale": "Cache hot redirects",
            "blocks": ["Cache", "API / Gateway"],
        },
        ["requirements", "apis"],
    )
    assert graded["mentor_summary"]
    assert graded["lesson"]["title"]
    assert graded["lesson"]["try_this"]
    assert len(graded["dimensions"]) == 6
    assert graded["weak_concepts"]


def test_pick_next_prefers_overlapping_concepts() -> None:
    db = MagicMock()
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    with patch(
        "app.services.system_design.list_problems",
        return_value=[
            {"id": a, "concept_keys": ["caching"], "difficulty": "easy", "sort_order": 1},
            {"id": b, "concept_keys": ["sharding", "availability"], "difficulty": "hard", "sort_order": 2},
        ],
    ):
        nxt = _pick_next_problem_id(
            db, concept_keys=["sharding"], exclude=uuid.UUID(a)
        )
    assert str(nxt) == b


def test_build_path_empty_history() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value.all.side_effect = [
        [
            {
                "key": "requirements",
                "title": "Requirements",
                "blurb": "x",
                "sort_order": 1,
                "prerequisites": [],
            }
        ],
        [],  # sessions
    ]
    # list_concepts then samples query — simplify by patching helpers
    with (
        patch(
            "app.services.system_design.list_concepts",
            return_value=[
                {
                    "key": "requirements",
                    "title": "Requirements",
                    "blurb": "x",
                    "sort_order": 1,
                    "prerequisites": [],
                }
            ],
        ),
        patch("app.services.system_design._concept_scores_for_subject", return_value={}),
    ):
        path = build_path(db, None, "abc")
    assert path["focus_key"] == "requirements"
    assert path["concepts"][0]["state"] == "not_started"


def test_recommend_problem_returns_something() -> None:
    db = MagicMock()
    pid = str(uuid.uuid4())
    with (
        patch(
            "app.services.system_design.build_path",
            return_value={"focus_key": "apis", "focus_title": "APIs"},
        ),
        patch(
            "app.services.system_design.list_problems",
            return_value=[
                {
                    "id": pid,
                    "slug": "url-shortener",
                    "title": "URL shortener",
                    "concept_keys": ["apis", "data"],
                    "difficulty": "easy",
                    "sort_order": 1,
                }
            ],
        ),
    ):
        db.execute.return_value.mappings.return_value.all.return_value = []
        rec = recommend_problem(db, None, "guest123")
    assert rec is not None
    assert rec["id"] == pid
