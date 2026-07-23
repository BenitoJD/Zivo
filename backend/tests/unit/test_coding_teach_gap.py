"""Unit tests for coding teach-gap helpers."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.services.coding_teach_gap import heuristic_teach_gap, pick_next_coding_id


def test_heuristic_fail_returns_lesson_shape() -> None:
    graded = heuristic_teach_gap(
        all_passed=False,
        passed=1,
        total=3,
        tags=["arrays", "two-pointers"],
        concept="Two sum",
        first_fail={"stdin": "1\n", "expected": "2\n", "stdout": "0\n", "stderr": ""},
    )
    assert graded["mentor_summary"]
    assert graded["lesson"]["title"]
    assert graded["lesson"]["body"]
    assert graded["lesson"]["try_this"]
    assert "arrays" in graded["weak_concepts"] or "two-pointers" in graded["weak_concepts"]


def test_heuristic_pass_still_teaches() -> None:
    graded = heuristic_teach_gap(
        all_passed=True,
        passed=3,
        total=3,
        tags=["dp"],
        concept="Knapsack",
        first_fail=None,
    )
    assert graded["lesson"]["try_this"]
    assert graded["weak_concepts"]


def test_pick_next_prefers_overlapping_tags() -> None:
    db = MagicMock()
    a = uuid.uuid4()
    b = uuid.uuid4()
    exclude = uuid.uuid4()
    db.execute.return_value.mappings.return_value.all.return_value = [
        {"id": a, "difficulty": "easy", "tags": ["math"], "concept": ""},
        {"id": b, "difficulty": "medium", "tags": ["two-pointers", "arrays"], "concept": "pair sum"},
    ]
    nxt = pick_next_coding_id(
        db,
        exclude=exclude,
        weak_concepts=["two-pointers"],
        tags=["arrays"],
        concept="",
    )
    assert nxt == b
