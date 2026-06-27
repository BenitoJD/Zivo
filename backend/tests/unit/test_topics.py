"""Explain feature — topic-outline parsing + finalization (pure logic)."""

from __future__ import annotations

from app.graphs.topics_graph import _finalize, _parse_topics, _slugify


def test_parse_plain_json_array() -> None:
    raw = '[{"title":"Photosynthesis","summary":"how plants make food"},{"title":"The Calvin Cycle"}]'
    out = _parse_topics(raw)
    assert [t["title"] for t in out] == ["Photosynthesis", "The Calvin Cycle"]
    assert out[0]["summary"] == "how plants make food"


def test_parse_fenced_and_surrounding_prose() -> None:
    raw = 'Here are the topics:\n```json\n[{"title":"Cell Membrane","summary":"a barrier"}]\n```\nDone.'
    out = _parse_topics(raw)
    assert out == [{"title": "Cell Membrane", "summary": "a barrier"}]


def test_parse_garbage_returns_empty() -> None:
    assert _parse_topics("no json here") == []
    assert _parse_topics("") == []
    assert _parse_topics("{not an array}") == []


def test_parse_skips_items_without_title() -> None:
    out = _parse_topics('[{"summary":"x"},{"title":"  "},{"title":"Real Topic"}]')
    assert [t["title"] for t in out] == ["Real Topic"]


def test_finalize_assigns_unique_slug_keys() -> None:
    topics = [{"title": "Energy Flow", "summary": "a"}, {"title": "Energy Flow!", "summary": "b"}]
    out = _finalize(topics)
    keys = [t["key"] for t in out]
    assert len(keys) == len(set(keys))  # unique
    assert keys[0] == "energy-flow"


def test_finalize_dedupes_identical_titles_and_caps() -> None:
    topics = [{"title": "Same", "summary": ""}] * 3 + [{"title": f"T{i}", "summary": ""} for i in range(20)]
    out = _finalize(topics)
    assert sum(1 for t in out if t["title"] == "Same") == 1
    assert len(out) <= 15


def test_slugify_handles_collisions_and_punctuation() -> None:
    taken: set[str] = set()
    assert _slugify("The Water Cycle!", taken) == "the-water-cycle"
    assert _slugify("The Water Cycle?", taken) == "the-water-cycle-2"
