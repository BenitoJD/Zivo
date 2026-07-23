"""Unit tests for curated coding bank helpers."""

from __future__ import annotations

from app.services.coding_curation import _normalize_tags, _slugify, build_full_payload
from app.services.coding_generation import _split_tests, public_payload


def test_split_tests_promotes_first_two_as_sample():
    tests = [
        {"stdin": "1\n", "expected_output": "1\n"},
        {"stdin": "2\n", "expected_output": "2\n"},
        {"stdin": "3\n", "expected_output": "3\n"},
    ]
    sample, hidden = _split_tests(tests)
    assert len(sample) == 2
    assert len(hidden) == 1
    assert hidden[0]["stdin"] == "3\n"


def test_public_payload_strips_hidden_and_solution():
    full = build_full_payload(
        title="T",
        statement="S",
        starter_code="print(0)",
        difficulty="easy",
        language_id=71,
        sample_tests=[{"stdin": "1\n", "expected_output": "1\n"}],
        hidden_tests=[{"stdin": "2\n", "expected_output": "2\n"}],
        editor_solution="print(1)",
        tags=["arrays"],
    )
    pub = public_payload(full)
    assert "hidden_tests" not in pub
    assert "editor_solution" not in pub
    assert pub["test_count"] == 1
    assert pub["tags"] == ["arrays"]
    assert pub["origin"] == "curated"


def test_slugify_and_tags():
    assert _slugify("Sum Two Integers!") == "sum-two-integers"
    assert _normalize_tags([" Arrays ", "arrays", "DP", ""]) == ["arrays", "dp"]
