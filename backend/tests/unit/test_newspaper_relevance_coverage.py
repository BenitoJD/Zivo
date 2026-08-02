"""Coverage for newspaper_relevance (LLM relevance judge)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.newspaper_relevance import _parse_relevance, judge_newspaper_relevance


def test_parse_relevance_valid() -> None:
    out = _parse_relevance('{"relevant": true, "theme": "polity", "rationale": "ok"}')
    assert out is not None and out["relevant"] is True


def test_parse_relevance_invalid() -> None:
    assert _parse_relevance("not json") is None
    assert _parse_relevance('{"theme": "x"}') is None  # missing relevant
    assert _parse_relevance("") is None


def test_judge_cache_hit() -> None:
    db = MagicMock()
    hit = {"relevant": True, "theme": "polity", "rationale": "cached"}
    with (
        patch("app.services.generation_cache.get", return_value=hit) as cg,
        patch("app.services.llm_router.acomplete_chat") as ac,
    ):
        out = judge_newspaper_relevance(db, "some page text")
    assert out == {"relevant": True, "theme": "polity", "rationale": "cached"}
    cg.assert_called_once()
    ac.assert_not_called()


def test_judge_empty_text() -> None:
    db = MagicMock()
    with (
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.generation_cache.put") as cp,
    ):
        out = judge_newspaper_relevance(db, "   ")
    assert out["relevant"] is False
    assert "Empty page" in out["rationale"]
    cp.assert_called_once()


def test_judge_llm_success() -> None:
    db = MagicMock()
    with (
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.generation_cache.put"),
        patch(
            "app.services.llm_sync.run_coro_in_worker",
            return_value='{"relevant": false, "theme": "sports", "rationale": "no"}',
        ),
    ):
        out = judge_newspaper_relevance(db, "cricket match report")
    assert out == {"relevant": False, "theme": "sports", "rationale": "no"}


def test_judge_llm_failure_degrades_open() -> None:
    db = MagicMock()
    with (
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.generation_cache.put"),
        patch(
            "app.services.llm_sync.run_coro_in_worker",
            side_effect=RuntimeError("provider down"),
        ),
    ):
        out = judge_newspaper_relevance(db, "some page")
    assert out["relevant"] is True
    assert "unavailable" in out["rationale"]


def test_judge_unparseable_degrades_open() -> None:
    db = MagicMock()
    with (
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.generation_cache.put"),
        patch("app.services.llm_sync.run_coro_in_worker", return_value="garbage"),
    ):
        out = judge_newspaper_relevance(db, "some page")
    assert out["relevant"] is True
    assert "no judgment" in out["rationale"]
