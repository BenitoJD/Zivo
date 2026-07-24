"""Tutor Retrieval Engine - gate, window, rank."""

from __future__ import annotations

from app.services.tutor_retrieval import (
    TUTOR_RETRIEVAL_VERSION,
    decide_retrieval,
    finish_ranked_chunks,
    is_conversational_followup,
    plan_rag_window,
)


def test_gate_skips_continuation() -> None:
    v = decide_retrieval("thanks", scope={}, has_history=True)
    assert v.retrieve is False
    assert v.reason == "continuation"
    assert v.policy_version == TUTOR_RETRIEVAL_VERSION


def test_gate_retrieves_first_turn() -> None:
    v = decide_retrieval("what is CAP?", scope={}, has_history=False)
    assert v.retrieve is True
    assert v.reason == "first_turn"


def test_gate_retrieves_with_page_scope() -> None:
    v = decide_retrieval("ok", scope={"current_page": 3}, has_history=True)
    assert v.retrieve is True
    assert v.reason == "current_page"


def test_conversational_followup() -> None:
    assert is_conversational_followup("go on")
    assert not is_conversational_followup("explain hashing")


def test_plan_rag_window_early_pages() -> None:
    plan = plan_rag_window(1, list(range(1, 101)))
    assert list(plan.pages) == [1, 2]
    assert plan.policy_version == TUTOR_RETRIEVAL_VERSION


def test_plan_rag_window_caps_at_max() -> None:
    plan = plan_rag_window(50, list(range(1, 101)), max_pages=6)
    assert len(plan.pages) == 6
    assert plan.pages[-1] == 52


def test_finish_ranked_chunks_truncates_without_rerank() -> None:
    chunks = [{"text": f"c{i}"} for i in range(10)]
    v = finish_ranked_chunks("q", chunks, top_n=3, rerank_enabled=False)
    assert len(v.chunks) == 3
    assert v.used_rerank is False
    assert v.chunks[0]["text"] == "c0"
