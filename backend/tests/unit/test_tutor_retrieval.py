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


def test_gate_skips_prefetched_reference() -> None:
    v = decide_retrieval(
        "long wikipedia message",
        scope={"reference_source": "wikipedia", "current_page": 3},
        has_history=False,
    )
    assert v.retrieve is False
    assert v.reason == "prefetched_reference"


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


def test_plan_brainstorm_sample_spans_document() -> None:
    from app.services.tutor_retrieval import plan_brainstorm_sample

    texts = [f"chunk-{i}" for i in range(48)]
    v = plan_brainstorm_sample(texts, sample_n=24)
    assert len(v.texts) == 24
    assert v.texts[0] == "chunk-0"
    assert v.texts[-1] == "chunk-46"


def test_compress_chat_history_keeps_tail() -> None:
    from app.services.tutor_retrieval import compress_chat_history

    prior = [{"role": "user", "content": f"msg-{i}"} for i in range(6)]
    v = compress_chat_history(prior)
    assert v.compressed is True
    assert len(v.messages) == 3
    assert "compressed" in v.messages[0]["content"]
    assert v.messages[-1]["content"] == "msg-5"


def test_decide_page_pin_prefers_current() -> None:
    from app.services.tutor_retrieval import decide_page_pin

    v = decide_page_pin({"current_page": 4})
    assert v.pin_current_first is True
    assert v.page == 4
    assert v.policy_version == TUTOR_RETRIEVAL_VERSION


def test_decide_page_pin_skips_without_current() -> None:
    from app.services.tutor_retrieval import decide_page_pin

    v = decide_page_pin({"page_start": 1, "page_end": 6})
    assert v.pin_current_first is False
    assert v.page is None


def test_decide_page_pin_rejects_invalid_page() -> None:
    from app.services.tutor_retrieval import decide_page_pin

    assert decide_page_pin({"current_page": 0}).pin_current_first is False
    assert decide_page_pin({"current_page": "nope"}).page is None


def test_evaluate_rag_window_ready_replans_stale_window() -> None:
    from app.services.tutor_retrieval import evaluate_rag_window_ready

    stale = evaluate_rag_window_ready(
        has_doc=True,
        current_page=10,
        saved_window=[4, 5, 6, 7, 8, 9],
        flag_ready=True,
        study_pages=list(range(2, 35)),
        ready_pages=set(range(2, 10)),
    )
    assert stale.ready is False
    assert stale.reason == "stale_window"
    assert 10 in stale.target_pages

    inside = evaluate_rag_window_ready(
        has_doc=True,
        current_page=8,
        saved_window=[4, 5, 6, 7, 8, 9],
        flag_ready=True,
        study_pages=list(range(2, 35)),
        ready_pages=set(),
    )
    assert inside.ready is True
    assert inside.reason == "flag_ready"


def test_plan_chunk_retrieval_pin_then_vector() -> None:
    from app.services.tutor_retrieval import PagePinVerdict, plan_chunk_retrieval

    pin = PagePinVerdict(pin_current_first=True, page=5)
    first = plan_chunk_retrieval(pin, {"current_page": 5, "page_start": 1, "page_end": 6}, "what is ATP?")
    assert first.strategy == "pin_page"
    assert first.page_start == 5
    miss = plan_chunk_retrieval(
        pin,
        {"current_page": 5, "page_start": 1, "page_end": 6},
        "what is ATP?",
        pin_missed=True,
        page_chunks_fetched=True,
        page_chunk_count=0,
    )
    assert miss.strategy == "vector_only"


def test_plan_tutor_weak_concepts() -> None:
    from app.services.tutor_retrieval import plan_tutor_weak_concepts

    plan = plan_tutor_weak_concepts({"strong": 2.0, "weak": -1.5, "mid": 0.1, "skip": None})
    assert plan.concepts[0][0] == "weak"
    assert len(plan.concepts) == 3


def test_plan_learn_context_rag() -> None:
    from app.services.tutor_retrieval import plan_learn_context_rag

    news = plan_learn_context_rag(4, list(range(1, 10)), newspaper=True)
    assert news.pages == (4,)
    upload = plan_learn_context_rag(1, list(range(1, 10)), newspaper=False)
    assert upload.pages[0] == 1
    assert len(upload.pages) > 1


def test_queue_rag_and_learn_session_attach() -> None:
    from app.services.tutor_retrieval import (
        plan_queue_rag_pages,
        should_attach_learn_session_context,
    )

    assert not should_attach_learn_session_context(scope_mode="read")
    assert should_attach_learn_session_context(scope_mode="learn")
    assert should_attach_learn_session_context(scope_mode=None)
    news = plan_queue_rag_pages(
        newspaper=True,
        current_page=2,
        study_pages=[1, 2, 3],
        stored_window=[9, 10],
    )
    assert 2 in news and 9 not in news
    stored = plan_queue_rag_pages(
        newspaper=False,
        current_page=2,
        study_pages=[1, 2, 3],
        stored_window=[9, 10],
    )
    assert stored == (9, 10)


def test_unconfirmed_tutor_policy() -> None:
    from app.services.tutor_retrieval import plan_unconfirmed_tutor_policy

    hint = plan_unconfirmed_tutor_policy(
        has_mcq_options=True, confirmed_choice_index=None
    )
    assert hint is not None and "hints only" in hint
    assert (
        plan_unconfirmed_tutor_policy(
            has_mcq_options=True, confirmed_choice_index=0
        )
        is None
    )
    assert (
        plan_unconfirmed_tutor_policy(
            has_mcq_options=False, confirmed_choice_index=None
        )
        is None
    )


def test_rag_chunk_split_defaults() -> None:
    from app.services.tutor_retrieval import (
        RAG_CHUNK_MAX_CHARS,
        RAG_CHUNK_OVERLAP,
        plan_rag_chunk_split,
    )

    plan = plan_rag_chunk_split()
    assert plan.max_chars == RAG_CHUNK_MAX_CHARS == 900
    assert plan.overlap == RAG_CHUNK_OVERLAP == 120
    custom = plan_rag_chunk_split(max_chars=400, overlap=40)
    assert custom.max_chars == 400 and custom.overlap == 40
    from app.services.tutor_retrieval import plan_cook_aspect_hint_context

    hint = plan_cook_aspect_hint_context()
    assert hint.hit_limit == 5 and hint.snippet_chars == 600
    from app.services.tutor_retrieval import plan_topic_explain_context

    topic = plan_topic_explain_context()
    assert topic.chunk_limit == 8
    from app.services.tutor_retrieval import plan_coding_assist_history, plan_chat_thread_history

    assert plan_coding_assist_history() == 12
    from app.services.tutor_retrieval import plan_coding_assist_code_chars

    assert plan_coding_assist_code_chars() == 12_000
    assert plan_chat_thread_history() == 6
    from app.services.tutor_retrieval import evaluate_chat_cache_eligible

    ok = evaluate_chat_cache_eligible(
        include_image=False,
        selection_text=None,
        has_citations=True,
        doc_count=1,
        has_history=False,
    )
    assert ok.eligible and ok.reason == "ok"
    blocked = evaluate_chat_cache_eligible(
        include_image=True,
        selection_text=None,
        has_citations=True,
        doc_count=1,
        has_history=False,
    )
    assert not blocked.eligible and blocked.reason == "image"
