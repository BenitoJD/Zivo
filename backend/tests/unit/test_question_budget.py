"""Unit tests for docs/QUESTION_BUDGET_ENGINE.md pure planner."""

from __future__ import annotations

import pytest

from app.services.question_budget import (
    BUDGET_VERSION,
    CEIL_PAGE,
    Unit,
    density_unit_count,
    information_floor_items,
    plan_document_budget,
    plan_page_budget,
)


def test_learn_one_per_central_unit() -> None:
    units = [
        Unit("a", "central"),
        Unit("b", "central"),
        Unit("c", "support"),
        Unit("ad", "skip"),
    ]
    plan = plan_page_budget(units, mode="learn")
    # 1+1+0.5+0 → 2.5 → 2
    assert plan.n_page == 2
    assert plan.n_cov == 2.5
    assert plan.budget_version == BUDGET_VERSION


def test_test_formative_multiplier() -> None:
    units = [Unit("a", "central"), Unit("b", "central")]
    plan = plan_page_budget(units, mode="test")
    # 2 * 3 = 6
    assert plan.n_page == 6


def test_non_content_is_zero() -> None:
    plan = plan_page_budget([Unit("a")], mode="learn", non_content=True)
    assert plan.n_page == 0


def test_ceil_cuts_large_plans() -> None:
    units = [Unit(f"u{i}", "central") for i in range(100)]
    plan = plan_page_budget(units, mode="test", ceil_page=CEIL_PAGE)
    assert plan.n_page == CEIL_PAGE


def test_density_prior_matches_legacy_spirit() -> None:
    assert density_unit_count(0, 0) == 0
    assert density_unit_count(119, 0) == 1
    assert density_unit_count(240, 10) == 2
    assert density_unit_count(600, 3) == 3


def test_density_fallback_when_no_units() -> None:
    plan = plan_page_budget(None, mode="learn", words=360, substantial_paragraphs=5)
    assert plan.n_page == 3
    assert plan.confidence == "medium"


def test_information_floor_formative() -> None:
    # SE=0.40 → I*=6.25; Ĩ=0.20 → 32
    assert information_floor_items() == 32


def test_document_test_applies_info_floor() -> None:
    learn = plan_document_budget([2, 3, 0], mode="learn")
    assert learn.n_doc == 5
    assert learn.n_info == 0

    test = plan_document_budget([2, 3, 0], mode="test")
    assert test.n_doc_cov == 5
    assert test.n_info == 32
    assert test.n_doc == 32


def test_information_floor_rejects_nonpositive() -> None:
    with pytest.raises(ValueError):
        information_floor_items(se_target=0)
