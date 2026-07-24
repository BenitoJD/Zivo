"""Aspect Discovery Engine - plan pick + speculative + next-unasked."""

from __future__ import annotations

from app.services.aspect_discovery import (
    ASPECT_DISCOVERY_VERSION,
    next_unasked,
    pick_for_plan,
    speculative_targets,
)


def test_pick_prefers_central() -> None:
    aspects = [
        {"key": "s1", "centrality": "support", "label": "S"},
        {"key": "c1", "centrality": "central", "label": "C"},
        {"key": "x1", "centrality": "skip", "label": "X"},
        {"key": "s2", "centrality": "support", "label": "S2"},
    ]
    v = pick_for_plan(aspects, n_page=2)
    keys = [a["key"] for a in v.aspects]
    assert keys[0] == "c1"
    assert "x1" not in keys
    assert v.policy_version == ASPECT_DISCOVERY_VERSION


def test_pick_keeps_all_centrals_even_if_over_n() -> None:
    aspects = [
        {"key": f"c{i}", "centrality": "central"} for i in range(4)
    ] + [{"key": "s1", "centrality": "support"}]
    v = pick_for_plan(aspects, n_page=2)
    assert v.n_kept == 4
    assert all(a["key"].startswith("c") for a in v.aspects)


def test_next_unasked() -> None:
    aspects = [
        {"key": "a", "asked": True},
        {"key": "b", "asked": False},
        {"key": "c", "asked": False},
    ]
    v = next_unasked(aspects, n=1)
    assert [a["key"] for a in v.aspects] == ["b"]


def test_speculative_from_paragraphs() -> None:
    text = "First idea here.\n\nSecond idea here.\n\nThird."
    v = speculative_targets(text, 3, 2)
    assert v.n_kept == 2
    assert all(a.get("speculative") for a in v.aspects)
    assert v.aspects[0]["key"].startswith("page-3-spec-")


def test_speculative_empty_page() -> None:
    v = speculative_targets("", 1, 2)
    assert v.n_kept == 0
