"""Aspect Discovery Engine - plan pick + speculative + next-unasked."""

from __future__ import annotations

from app.services.aspect_discovery import (
    ASPECT_DISCOVERY_VERSION,
    next_unasked,
    parse_centrality,
    pick_for_plan,
    speculative_targets,
)


def test_parse_centrality_peripheral_is_support() -> None:
    assert parse_centrality("peripheral") == "support"
    assert parse_centrality("secondary") == "support"
    assert parse_centrality("skip") == "skip"
    assert parse_centrality("central") == "central"


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


def test_dedupe_aspects_clusters_near_duplicates() -> None:
    from unittest.mock import patch

    from app.services.aspect_discovery import ASPECT_CLUSTER_THRESHOLD, dedupe_aspects

    aspects = [
        {"key": "a", "label": "Osmosis in plants"},
        {"key": "b", "label": "Osmosis in plant cells"},
        {"key": "c", "label": "Mitosis stages"},
    ]

    def fake_embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            if "mitosis" in t.lower():
                out.append([0.0, 1.0])
            else:
                out.append([1.0, 0.0])
        return out

    with patch("app.services.mcq_dedup.embed_texts", side_effect=fake_embed):
        v = dedupe_aspects(aspects, threshold=0.99)
    assert v.deduped_count == 2
    assert v.raw_count == 3
    assert len(v.merged_keys) == 1
    assert v.threshold == 0.99
    assert ASPECT_CLUSTER_THRESHOLD == 0.88
    assert v.policy_version == ASPECT_DISCOVERY_VERSION
