"""Unit suite for Year-1 engines 5–13 facades."""

from __future__ import annotations

from app.services.content_worthiness import evaluate_worthiness
from app.services.grounding_answerability import evaluate_grounding
from app.services.item_health import evaluate_item_health
from app.services.kc_coverage import coverage_state, normalize_aspects
from app.services.mastery_evidence import evaluate_stop
from app.services.misconception_distractor import evaluate_distractors
from app.services.question_graph import LINK_FOLLOW_UP_AFTER_MISS, plan_batch_lineage
from app.services.session_design import plan_session
from app.services.spaced_revisit import plan_revisit


def test_kc_coverage_complete_when_central_asked() -> None:
    plan = normalize_aspects(
        [{"key": "Osmosis!", "label": "Osmosis", "central": True},
         {"key": "skip-me", "label": "Skip", "central": False}]
    )
    assert plan.aspects[0].key == "osmosis"
    st = coverage_state(plan, ["osmosis"])
    assert st.complete
    assert st.uncovered_central == ()
    from app.services.kc_coverage import (
        evaluate_page_coverage_complete,
        mark_aspects_answered,
        should_persist_coverage_complete,
    )

    flagged = evaluate_page_coverage_complete(
        {"aspects": [{"key": "osmosis", "central": True}], "coverage_complete": True}
    )
    assert flagged.complete and flagged.reason == "flag"
    empty = evaluate_page_coverage_complete({"coverage_complete": True})
    assert empty.complete is False and empty.reason == "no_aspects"
    assert should_persist_coverage_complete({"aspects": [{"key": "a"}]})
    assert not should_persist_coverage_complete({})
    marked = mark_aspects_answered(
        [{"key": "osmosis", "answered": False}, {"key": "other", "answered": False}],
        "osmosis",
    )
    assert marked[0]["answered"] is True
    assert marked[1]["answered"] is False
    from app.services.kc_coverage import evaluate_aspect_exhaustion_close

    assert evaluate_aspect_exhaustion_close(has_aspects=True, unasked_count=0)
    assert not evaluate_aspect_exhaustion_close(has_aspects=False, unasked_count=0)
    from app.services.kc_coverage import evaluate_stale_coverage_stamp, stamp_kc_centrality

    assert stamp_kc_centrality(orig_centrality="skip", central=True) == ("skip", False)
    assert stamp_kc_centrality(orig_centrality="central", central=False) == ("support", False)
    from app.services.kc_coverage import stamp_aspect_flags

    assert stamp_aspect_flags("central") == (True, False)
    assert stamp_aspect_flags("support") == (False, True)
    assert stamp_aspect_flags("skip") == (False, False)
    from app.services.kc_coverage import coverage_aspects_field

    assert coverage_aspects_field("test") == "test_aspects"
    assert coverage_aspects_field("learn") == "aspects"
    assert evaluate_stale_coverage_stamp({"coverage_complete": True}, mcq_count=0)
    assert not evaluate_stale_coverage_stamp(
        {"coverage_complete": True, "aspects": [{"key": "a"}]}, mcq_count=0
    )
    from app.services.kc_coverage import normalize_concept_label

    assert normalize_concept_label("") == "General"
    assert normalize_concept_label("Chlorophyll is the green pigment in plants that") == (
        "Chlorophyll"
    )


def test_mastery_stop_learn_and_test() -> None:
    early = evaluate_stop(ability=3.0, n=1, mode="learn")
    assert early.stop is False and early.reason == "min_items"
    mastered = evaluate_stop(ability=3.0, n=5, mode="learn")
    assert mastered.stop is True and mastered.reason == "mastery"
    test = evaluate_stop(ability=0.0, n=10, se_theta=0.3, mode="test")
    assert test.stop is True and test.reason == "se_precision"


def test_question_graph_plans_follow_up_and_harder() -> None:
    plan = plan_batch_lineage(
        [
            {"_assertion_id": "11111111-1111-1111-1111-111111111111", "primary_concept_key": "a"},
            {"_assertion_id": "22222222-2222-2222-2222-222222222222", "primary_concept_key": "a"},
            {"_assertion_id": "33333333-3333-3333-3333-333333333333", "primary_concept_key": "b"},
        ]
    )
    kinds = {e.kind for e in plan.edges}
    assert LINK_FOLLOW_UP_AFTER_MISS in kinds
    assert "harder_than" in kinds
    by_kind = {e.kind: e.confidence for e in plan.edges}
    assert by_kind[LINK_FOLLOW_UP_AFTER_MISS] == 0.85
    assert by_kind["harder_than"] == 0.7


def test_question_graph_mcq_reuse_scope() -> None:
    from app.services.question_graph import GRAPH_VERSION, plan_mcq_reuse

    off = plan_mcq_reuse("off")
    assert off.enabled is False
    assert off.scope == "off"
    demo = plan_mcq_reuse("demo")
    assert demo.enabled is True and demo.demo_only is True
    all_scope = plan_mcq_reuse("all")
    assert all_scope.enabled is True and all_scope.demo_only is False
    assert all_scope.policy_version == GRAPH_VERSION
    from app.services.question_graph import filter_reusable_templates

    kept = filter_reusable_templates(
        [
            {"artifact_id": "a", "page_number": 1, "question": "Q1", "options": ["a"]},
            {"artifact_id": "a", "page_number": 1, "question": "Q2", "options": ["b"]},
            {"artifact_id": "b", "page_number": 2, "question": "Q3", "options": ["c"]},
            {"artifact_id": "a", "page_number": 1, "question": "", "options": ["d"]},
        ]
    )
    assert [p["question"] for p in kept] == ["Q1", "Q2"]


def test_distractors_flag_longest_correct() -> None:
    v = evaluate_distractors(
        ["short", "also", "this is the conspicuously longest correct option text"],
        [2],
    )
    assert "longest_option_correct" in v.flaw_codes or v.scores.plausibility < 1.0


def test_worthiness_rejects_ads_and_short() -> None:
    assert evaluate_worthiness(page_text="hi").worthy is False
    assert evaluate_worthiness(page_text="Subscribe now for limited time offer!!!").worthy is False
    assert evaluate_worthiness(
        page_text="Photosynthesis converts light energy into chemical energy in chloroplasts."
    ).worthy is True


def test_worthiness_vision_glance_maps_usable() -> None:
    from app.services.content_worthiness import WORTH_VERSION, evaluate_vision_glance

    usable = evaluate_vision_glance(usable=True, rationale="Diagram of cell cycle")
    assert usable.worthy is False
    assert usable.reason == "empty_vision_usable"
    assert "Diagram" in usable.details
    blank = evaluate_vision_glance(usable=False)
    assert blank.reason == "empty_vision_blank"
    assert blank.policy_version == WORTH_VERSION


def test_worthiness_junk_and_newspaper_seam() -> None:
    from app.services.content_worthiness import looks_like_junk

    assert looks_like_junk("....,,,,;;;; 123 456 789 %%%%") is True
    junk = evaluate_worthiness(
        page_text="....,,,,;;;; 123 456 789 %%%% more noise !!",
        check_junk=True,
        min_chars=24,
    )
    assert junk.worthy is False and junk.reason == "junk_text"
    ad = evaluate_worthiness(
        page_text="Advertisement: buy now limited period offer call toll free flat for sale",
        newspaper=True,
    )
    assert ad.worthy is False
    assert ad.reason.startswith("newspaper_")
    from app.services.content_worthiness import (
        evaluate_digest_page_worthy,
        evaluate_llm_triage_units,
        plan_newspaper_batch_gate,
    )

    zero = evaluate_llm_triage_units(content_type="non_content", usable=True, aspect_count=3)
    assert zero.zero_question and zero.reason == "non_content"
    ok = evaluate_llm_triage_units(content_type="prose", usable=True, aspect_count=2)
    assert ok.zero_question is False
    assert evaluate_digest_page_worthy(mcq_count=2, page_text="ignored")
    skip = plan_newspaper_batch_gate(
        page_text="Advertisement: buy now limited period offer call toll free flat for sale"
    )
    assert skip.skip is True and skip.mark_complete is True


def test_practice_selection_overlap() -> None:
    from app.services.practice_selection import PracticeCandidate, pick_next

    pick = pick_next(
        [
            PracticeCandidate("a", ("caching",), "easy"),
            PracticeCandidate("b", ("queues", "caching"), "hard"),
            PracticeCandidate("c", ("apis",), "medium"),
        ],
        ["caching"],
        exclude_id="a",
    )
    assert pick.id == "b"
    assert pick.score >= 10


def test_grounding_overlap() -> None:
    page = "Chloroplasts perform photosynthesis using chlorophyll."
    ok = evaluate_grounding(
        stem="What do chloroplasts perform?",
        correct_texts=["photosynthesis"],
        page_text=page,
    )
    assert ok.grounded and ok.score > 0
    bad = evaluate_grounding(
        stem="Who invented the telephone?",
        correct_texts=["Alexander Graham Bell"],
        page_text=page,
        min_score=0.2,
    )
    assert bad.grounded is False


def test_grounding_cook_fatality_owns_page_density() -> None:
    from app.services.grounding_answerability import evaluate_grounding_for_cook

    thin = evaluate_grounding_for_cook(
        stem="Who invented the telephone?",
        correct_texts=["Alexander Graham Bell"],
        page_text="short page",
    )
    assert thin.fatal is False
    fat = evaluate_grounding_for_cook(
        stem="Who invented the telephone?",
        correct_texts=["Alexander Graham Bell"],
        page_text=("Chloroplasts perform photosynthesis using chlorophyll. " * 8),
    )
    assert fat.fatal is True
    assert "not_grounded" in fat.flaw_codes


def test_aspect_abandon_and_fallback() -> None:
    from app.services.aspect_discovery import (
        heuristic_fallback_aspects,
        should_abandon_aspect,
    )

    assert should_abandon_aspect(2).abandon is False
    assert should_abandon_aspect(3).abandon is True
    text = ("Idea one about cells.\n\n" * 20) + ("Another paragraph with enough words here.\n\n" * 5)
    pick = heuristic_fallback_aspects(text, page_number=2)
    assert pick.n_kept >= 1
    from app.services.aspect_discovery import substantial_paragraphs

    dense = substantial_paragraphs("Short.\n\n" + ("word " * 20))
    assert len(dense) == 1


def test_empty_page_reselect_and_serve_schedule() -> None:
    from app.services.content_worthiness import plan_empty_page_reselect
    from app.services.session_design import evaluate_serve_schedule, plan_interview_rounds
    from app.services.mastery_evidence import label_path_mastery

    blank = plan_empty_page_reselect(prior_streak=4, vision_usable=False)
    assert blank.prompt_reselect is True
    vision = plan_empty_page_reselect(prior_streak=0, vision_usable=True)
    assert vision.reason == "unreadable_content"
    sched = evaluate_serve_schedule(ready_count=2, answered_on_page=5, page_budget=10)
    assert sched.refill_now is True
    assert sched.prefetch_transition is True
    assert sched.periodic_refill is False
    periodic = evaluate_serve_schedule(ready_count=20, answered_on_page=2)
    assert periodic.periodic_refill is True
    assert periodic.refill_now is False
    product = plan_interview_rounds("product")
    assert product.category == "product"
    assert product.rounds[0]["kind"] == "coding"
    assert plan_interview_rounds("nope").category == "other"
    assert label_path_mastery([0.9, 0.8]).state == "strong"
    assert label_path_mastery([]).state == "not_started"
    from app.services.mastery_evidence import (
        plan_progress_topic_display_limit,
        plan_sd_path_sample_limit,
    )

    assert plan_sd_path_sample_limit() == 40
    assert plan_progress_topic_display_limit() == 24
    from app.services.mastery_evidence import plan_progress_recent_days

    assert plan_progress_recent_days() == 14
    assert plan_progress_recent_days(200) == 90


def test_tutor_cache_and_brainstorm_gate() -> None:
    from app.services.tutor_retrieval import (
        compress_chat_history,
        decide_cache_reuse,
        decide_retrieval,
        plan_brainstorm_sample,
    )

    assert decide_cache_reuse(0.95).reuse is True
    assert decide_cache_reuse(0.5).reuse is False
    brainstorm = decide_retrieval("hello", scope={"mode": "brainstorm"}, has_history=True)
    assert brainstorm.retrieve is False and brainstorm.reason == "brainstorm"
    sample = plan_brainstorm_sample([f"c{i}" for i in range(100)])
    assert len(sample.texts) == sample.sample_n
    hist = compress_chat_history(
        [{"role": "user", "content": f"m{i}"} for i in range(6)]
    )
    assert hist.compressed is True
    assert len(hist.messages) == 3


def test_speculative_budget_and_naming_gate() -> None:
    from app.services.newspaper_naming import evaluate_naming_confidence
    from app.services.question_budget import speculative_page_budget

    seed = speculative_page_budget(mode="learn")
    assert seed.confidence == "low"
    assert seed.n_page == 5
    soft = evaluate_naming_confidence(0.4, exact_catalog_hit=False)
    assert soft.accept_identity is True and soft.learn_alias is False
    exact = evaluate_naming_confidence(0.4, exact_catalog_hit=True)
    assert exact.accept_identity is True and exact.learn_alias is True
    reject = evaluate_naming_confidence(0.2, exact_catalog_hit=False)
    assert reject.accept_identity is False


def test_spaced_revisit_expands_on_success() -> None:
    hit = plan_revisit(last_correct=True, repetitions=2, prior_interval_hours=72, ease=2.5)
    miss = plan_revisit(last_correct=False, repetitions=2, prior_interval_hours=72, ease=2.5)
    assert hit.next_due_hours > miss.next_due_hours
    assert miss.repetitions == 0


def test_session_design_caps() -> None:
    from app.services.session_design import evaluate_session_break

    s = plan_session(100, soft_cap=20, mode="learn")
    assert s.n_session == 20
    t = plan_session(100, soft_cap=20, mode="test")
    assert t.n_session == 25
    full = evaluate_session_break(session_items=20, n_session=s.n_session)
    assert full.should_break is True and full.reason == "session_full"
    mid = evaluate_session_break(session_items=3, n_session=s.n_session)
    assert mid.should_break is False and mid.reason == "in_session"
    none = evaluate_session_break(session_items=5, n_session=0)
    assert none.should_break is False and none.reason == "no_cap"
    from app.services.session_design import (
        evaluate_empty_batch_coverage_close,
        evaluate_learn_cook_coverage_close,
        evaluate_newspaper_triage_complete,
    )

    assert evaluate_learn_cook_coverage_close(hit_budget=True, serve_mode="learn")
    assert not evaluate_learn_cook_coverage_close(hit_budget=True, serve_mode="test")
    assert evaluate_empty_batch_coverage_close(
        saved=0, start_sequence=4, batch_size=2, budget=6, has_targets=False, has_aspects=True
    )
    assert not evaluate_empty_batch_coverage_close(
        saved=1, start_sequence=4, batch_size=2, budget=6, has_targets=False, has_aspects=True
    )
    assert evaluate_newspaper_triage_complete(has_any_coverage=False, questions_generated=2)
    assert not evaluate_newspaper_triage_complete(has_any_coverage=False, questions_generated=0)
    from app.services.session_design import (
        alias_debuggable,
        evaluate_auxiliary_cook_spawn,
        evaluate_background_first_batch,
        plan_first_cook_batch,
        plan_transition_next,
    )

    spawn = evaluate_auxiliary_cook_spawn(programmable=True, debuggable=False)
    assert spawn.spawn_coding is True and spawn.spawn_debug is False
    assert alias_debuggable(programmable=True, debuggable=None) is True
    assert plan_first_cook_batch(budget=6, first_batch=1) == 1
    assert plan_first_cook_batch(budget=0, first_batch=1) == 0
    assert evaluate_background_first_batch(budget=6, generated=0, first_batch=1) == 1
    assert evaluate_background_first_batch(budget=6, generated=2, first_batch=1) == 0
    nxt = plan_transition_next(
        has_next=True,
        next_has_coverage=True,
        next_generated=0,
        generate_next_page=True,
        current_budget=6,
    )
    assert nxt.cook_next is True and nxt.triage_next is False
    triage = plan_transition_next(
        has_next=True,
        next_has_coverage=False,
        next_generated=0,
        generate_next_page=True,
        current_budget=6,
    )
    assert triage.triage_next is True and triage.cook_next is False
    from app.services.session_design import evaluate_serial_cook_fallback

    assert evaluate_serial_cook_fallback(saved=0, has_targets=True)
    assert not evaluate_serial_cook_fallback(saved=1, has_targets=True)
    assert not evaluate_serial_cook_fallback(saved=0, has_targets=False)
    from app.services.session_design import (
        evaluate_newspaper_learn_complete_counts,
        plan_eager_triage_pages,
        plan_newspaper_ingest_batch,
        plan_page_advance_next,
        should_require_rag_on_page_advance,
    )

    assert plan_eager_triage_pages(
        from_page=2, study_pages=[1, 2, 3, 4, 5, 6, 7], lookahead=3
    ) == (3, 4, 5)
    assert plan_eager_triage_pages(
        from_page=2,
        study_pages=[1, 2, 3, 4, 5],
        lookahead=3,
        covered_pages=[3],
        active_triage_pages=[4],
    ) == (5,)
    assert plan_eager_triage_pages(from_page=9, study_pages=[1, 2, 3]) == ()
    assert plan_page_advance_next(
        has_coverage=False, generated=0, rag_ready=False
    ).action == "triage"
    assert plan_page_advance_next(
        has_coverage=True, generated=0, rag_ready=False
    ).action == "cook_first_batch"
    assert plan_page_advance_next(
        has_coverage=True, generated=2, rag_ready=False
    ).action == "ingest_rag"
    assert plan_page_advance_next(
        has_coverage=True, generated=2, rag_ready=True
    ).action == "noop"
    assert not should_require_rag_on_page_advance(has_coverage=False, generated=0)
    assert not should_require_rag_on_page_advance(has_coverage=True, generated=0)
    assert should_require_rag_on_page_advance(has_coverage=True, generated=2)
    assert plan_newspaper_ingest_batch([1, 2, 3, 4, 5, 6, 7, 8, 9]) == (
        1, 2, 3, 4, 5, 6, 7, 8
    )
    assert plan_newspaper_ingest_batch([]) == ()
    assert evaluate_newspaper_learn_complete_counts(answered_count=3, pool_count=3)
    assert not evaluate_newspaper_learn_complete_counts(answered_count=2, pool_count=3)
    assert not evaluate_newspaper_learn_complete_counts(answered_count=0, pool_count=0)
    from datetime import date

    from app.services.session_design import (
        evaluate_edition_practice_window,
        plan_newspaper_serve_scope,
    )

    news = plan_newspaper_serve_scope(newspaper=True)
    assert news.stats_from_edition and news.selection_from_page and news.edition_budget
    upload = plan_newspaper_serve_scope(newspaper=False)
    assert not upload.stats_from_edition and not upload.edition_budget
    cutoff = date(2026, 8, 1)
    assert evaluate_edition_practice_window(cutoff, cutoff=cutoff)
    assert not evaluate_edition_practice_window(date(2026, 7, 31), cutoff=cutoff)
    from app.services.session_design import (
        REFILL_BATCH_SIZE,
        plan_newspaper_hub_heal,
        plan_newspaper_uncooked_followup,
        plan_refill_batch,
    )

    assert plan_refill_batch(remaining=12) == REFILL_BATCH_SIZE
    assert plan_refill_batch(remaining=2) == 2
    assert plan_refill_batch(remaining=0) == 0
    assert plan_refill_batch(remaining=-3) == 0
    assert plan_newspaper_hub_heal(doc_status="indexing") == "reingest"
    assert plan_newspaper_hub_heal(doc_status="pending") == "reingest"
    assert plan_newspaper_hub_heal(doc_status="ready") == "recook"
    assert plan_newspaper_hub_heal(doc_status="retracted") == "idle"
    assert plan_newspaper_uncooked_followup(cook_enqueued=True) == "wait"
    assert plan_newspaper_uncooked_followup(cook_enqueued=False) == "promote_ready"
    from app.services.session_design import (
        evaluate_generation_batch_outcome,
        plan_interview_advance,
    )

    empty = evaluate_generation_batch_outcome(saved=0, has_targets=True)
    assert empty.activity_status == "failed" and empty.error_code == "generation_empty"
    ok = evaluate_generation_batch_outcome(saved=0, has_targets=False)
    assert ok.activity_status == "succeeded" and ok.error_code is None
    saved = evaluate_generation_batch_outcome(saved=2, has_targets=True)
    assert saved.activity_status == "succeeded"
    nxt = plan_interview_advance(
        round_index=0, q_in_round=0, round_question_count=2, round_count=2
    )
    assert nxt.complete is False and nxt.round_index == 0 and nxt.q_in_round == 1
    wrap = plan_interview_advance(
        round_index=0, q_in_round=1, round_question_count=2, round_count=2
    )
    assert wrap.complete is False and wrap.round_index == 1 and wrap.q_in_round == 0
    done = plan_interview_advance(
        round_index=1, q_in_round=1, round_question_count=2, round_count=2
    )
    assert done.complete is True and done.round_index == 2
    from app.services.session_design import plan_interview_gen_contract

    mcq = plan_interview_gen_contract("mcq")
    assert mcq.kind == "mcq" and "correct_index" in mcq.schema
    assert mcq.max_options == 6 and mcq.max_tests == 0
    coding = plan_interview_gen_contract("coding")
    assert coding.kind == "coding" and "starter_code" in coding.schema
    assert coding.max_tests == 6 and coding.max_options == 0
    typed = plan_interview_gen_contract("behavioural")
    assert typed.kind == "typed" and typed.schema == '{"question":"..."}'
    from app.services.session_design import plan_study_range_heal

    idle = plan_study_range_heal(
        counted_pages=1, selected_range={"pages": [1]}, doc_status="ready"
    )
    assert not idle.clear_selected_range and not idle.reset_to_pending
    heal = plan_study_range_heal(
        counted_pages=8, selected_range={"pages": [1]}, doc_status="ready"
    )
    assert heal.clear_selected_range and heal.reset_to_pending
    from_to = plan_study_range_heal(
        counted_pages=4, selected_range={"from": 1, "to": 1}, doc_status="pending"
    )
    assert from_to.clear_selected_range and not from_to.reset_to_pending
    keep = plan_study_range_heal(
        counted_pages=4, selected_range={"from": 2, "to": 4}, doc_status="ready"
    )
    assert not keep.clear_selected_range
    from app.services.session_design import (
        FLASHCARD_MAX,
        INDEXING_RECOVERY_GRACE_MINUTES,
        NEWSPAPER_RETENTION_DAYS,
        OPTION_COACH_PAGE_LIMIT,
        QUIZ_MAX_QUESTIONS,
        clamp_auxiliary_count,
        evaluate_auxiliary_output,
        evaluate_option_coach_eligible,
        plan_auxiliary_artifact_cap,
        plan_auxiliary_generation_strategy,
        plan_auxiliary_map_concurrency,
        plan_auxiliary_chunk_load_limit,
        plan_ingest_recovery_schedule,
        plan_auxiliary_field_caps,
        plan_learner_list_cap,
        plan_option_coach_page_limit,
        plan_grade_feedback_timeout,
        plan_bundle_upload,
        plan_interview_asked_context,
        plan_brainstorm_tree_depth,
        plan_newspaper_recovery_batch,
        plan_newspaper_edition_questions_limit,
    )

    cards = plan_auxiliary_artifact_cap("flashcards")
    assert cards.max_count == FLASHCARD_MAX == 24
    palace = plan_auxiliary_artifact_cap("memory_palace")
    assert palace.min_count == 4 and palace.max_count == 8
    assert clamp_auxiliary_count("quiz", 999) == QUIZ_MAX_QUESTIONS
    assert clamp_auxiliary_count("quiz", 0) == 10
    recover = plan_ingest_recovery_schedule()
    assert recover.indexing_grace_minutes == INDEXING_RECOVERY_GRACE_MINUTES
    assert recover.prepping_batch == 25
    assert NEWSPAPER_RETENTION_DAYS == 30
    fields = plan_auxiliary_field_caps("flashcards")
    assert fields.limit("front") == 400 and fields.limit("back") == 600
    assert plan_learner_list_cap("saved_notes") == 500
    assert plan_learner_list_cap("brainstorm") == 500
    assert plan_option_coach_page_limit() == OPTION_COACH_PAGE_LIMIT == 60
    single = plan_auxiliary_generation_strategy(10, single_shot_max_tokens=100)
    assert single.mode == "single_shot" and single.single_shot_max_tokens == 100
    mapped = plan_auxiliary_generation_strategy(101, single_shot_max_tokens=100)
    assert mapped.mode == "map_reduce"
    bundle = plan_bundle_upload()
    assert bundle.min_files == 2 and bundle.max_files == 20
    assert plan_interview_asked_context() == 8
    assert plan_brainstorm_tree_depth() == 12
    keep = evaluate_auxiliary_output("memory_palace", 4)
    assert keep.keep and keep.reason == "ok"
    reject = evaluate_auxiliary_output("memory_palace", 3)
    assert not reject.keep and reject.reason == "below_min"
    assert plan_auxiliary_map_concurrency("notes") == 6
    assert plan_auxiliary_map_concurrency("audiobook") == 4
    assert plan_grade_feedback_timeout() == 12
    assert evaluate_option_coach_eligible(is_multi=False)
    assert not evaluate_option_coach_eligible(is_multi=True)
    assert plan_newspaper_recovery_batch() == 10
    assert plan_auxiliary_chunk_load_limit() == 200
    assert plan_newspaper_edition_questions_limit() == 40
    assert plan_newspaper_edition_questions_limit(99) == 80


def test_item_health_retire_and_keep() -> None:
    bad = evaluate_item_health(p_correct=0.0, n_exposure=20)
    assert bad.action == "retire"
    good = evaluate_item_health(p_correct=0.6, n_exposure=20, r_pbis=0.35)
    assert good.action == "keep"


def test_session_soft_matches_budget_constant() -> None:
    from app.services.question_budget import SESSION_SOFT
    from app.services.session_design import SESSION_SOFT_DEFAULT

    assert SESSION_SOFT_DEFAULT == SESSION_SOFT


def test_prep_progress_blend_and_cook_schedule() -> None:
    from app.services.session_design import (
        PREP_COOK_WEIGHT,
        PREP_INDEX_WEIGHT,
        evaluate_background_cook_tick,
        evaluate_newspaper_edition_tick,
        evaluate_prep_progress,
    )

    indexing = evaluate_prep_progress(
        index_pct=50, cook_pct=0, phase="indexing", has_study_pages=True
    )
    assert indexing.phase == "indexing"
    assert indexing.overall_pct == 50
    cooking = evaluate_prep_progress(
        index_pct=100, cook_pct=20, phase="cooking", has_study_pages=True
    )
    assert cooking.overall_pct == int(100 * PREP_INDEX_WEIGHT + 20 * PREP_COOK_WEIGHT)
    empty = evaluate_prep_progress(
        index_pct=80, cook_pct=10, phase="indexing", has_study_pages=False
    )
    assert empty.overall_pct == 0

    wait = evaluate_background_cook_tick(
        phase="indexing",
        all_indexed=False,
        has_ingest_missing=False,
        triage_page=None,
        cook_page=None,
        total_generated=0,
        max_questions=200,
        remaining_on_page=None,
    )
    assert wait.action == "idle"
    cap = evaluate_background_cook_tick(
        phase="cooking",
        all_indexed=True,
        has_ingest_missing=False,
        triage_page=None,
        cook_page=3,
        total_generated=200,
        max_questions=200,
        remaining_on_page=4,
    )
    assert cap.action == "complete_cap"
    cook = evaluate_background_cook_tick(
        phase="cooking",
        all_indexed=True,
        has_ingest_missing=False,
        triage_page=None,
        cook_page=3,
        total_generated=10,
        max_questions=200,
        remaining_on_page=4,
    )
    assert cook.action == "cook" and cook.page == 3

    edition = evaluate_newspaper_edition_tick(
        has_ingest_missing=True,
        cook_page=2,
        cook_mode="learn",
        remaining=5,
        triage_page=1,
    )
    assert edition.action == "cook"
    assert edition.also_ingest is True
    triage = evaluate_newspaper_edition_tick(
        has_ingest_missing=True,
        cook_page=None,
        cook_mode=None,
        remaining=0,
        triage_page=4,
    )
    assert triage.action == "triage" and triage.page == 4


def test_page_complete_and_catalog_ready() -> None:
    from app.services.session_design import (
        evaluate_newspaper_catalog_ready,
        evaluate_newspaper_learn_complete,
        evaluate_page_complete,
        plan_interview_question_shape,
    )

    cover = evaluate_page_complete(
        non_content=True,
        has_next_card=True,
        all_served_answered=False,
        has_active_generate=False,
        generated=0,
        budget=6,
        coverage_done=False,
        generation_pending=True,
    )
    assert cover.complete is True
    pending = evaluate_page_complete(
        non_content=False,
        has_next_card=False,
        all_served_answered=False,
        has_active_generate=False,
        generated=2,
        budget=6,
        coverage_done=False,
        generation_pending=True,
    )
    assert pending.complete is False
    done = evaluate_page_complete(
        non_content=False,
        has_next_card=False,
        all_served_answered=True,
        has_active_generate=False,
        generated=2,
        budget=6,
        coverage_done=False,
        generation_pending=True,
    )
    assert done.complete is True
    assert evaluate_newspaper_learn_complete(
        learn_pool_count=3, all_learn_answered=True, generation_pending=False
    )
    assert evaluate_newspaper_catalog_ready(
        is_newspaper=True, has_coverage=True, budget=6, non_content=False, page1_mcq_count=1
    )
    assert not evaluate_newspaper_catalog_ready(
        is_newspaper=True, has_coverage=True, budget=6, non_content=False, page1_mcq_count=0
    )
    assert plan_interview_question_shape(
        planned_kind="mcq", mcq_valid=False, coding_has_tests=True
    ) == "typed"
    assert plan_interview_question_shape(
        planned_kind="coding", mcq_valid=True, coding_has_tests=False
    ) == "coding_fallback"


def test_path_focus_and_newspaper_cook_gate() -> None:
    from app.services.content_worthiness import evaluate_empty_study_reason, evaluate_newspaper_cook_gate
    from app.services.mastery_evidence import plan_path_focus, sample_path_mastery

    assert plan_path_focus([("a", "strong"), ("b", "needs_work")]) == "b"
    assert plan_path_focus([("a", "strong")]) == "a"
    weak = sample_path_mastery(dimension_scores=[4, 4, 4, 4], is_weak=True)
    strong = sample_path_mastery(dimension_scores=[4, 4, 4, 4], is_weak=False)
    assert weak < strong
    ad = evaluate_newspaper_cook_gate("LIMITED PERIOD OFFER buy now call toll free 1800123456. Subscribe now scan QR. Advertisement for cars. Another advertisement.", relevance={"relevant": True})
    assert ad.label == "ad"
    editorial = (
        "The central bank raised rates by 25 basis points yesterday, "
        "citing persistent inflation in food and fuel. Markets reacted "
        "cautiously as bond yields edged higher across the curve. "
        "Economists said the move was widely anticipated after recent data."
    )
    skipped = evaluate_newspaper_cook_gate(editorial, relevance=None)
    assert skipped.label == "cook"
    off = evaluate_newspaper_cook_gate(editorial, relevance={"relevant": False, "rationale": "sports"})
    assert off.label == "off_syllabus"
    from app.services.content_worthiness import evaluate_newspaper_relevance_outcome

    empty = evaluate_newspaper_relevance_outcome(page_text="  ")
    assert empty.relevant is False and empty.reason == "empty"
    outage = evaluate_newspaper_relevance_outcome(page_text="editorial", judge_error=True)
    assert outage.relevant is True and outage.reason == "judge_unavailable"
    none = evaluate_newspaper_relevance_outcome(page_text="editorial", parsed=None)
    assert none.relevant is True and none.reason == "no_judgment"
    judged = evaluate_newspaper_relevance_outcome(
        page_text="editorial",
        parsed={"relevant": False, "theme": "sports", "rationale": "x"},
    )
    assert judged.relevant is False and judged.reason == "judged"
    assert evaluate_empty_study_reason(
        document_complete=True,
        newspaper=False,
        questions_generated=0,
        questions_answered=0,
        generation_pending=False,
        range_has_no_questions=True,
    ) == "no_testable_content"
    from app.services.content_worthiness import (
        JUNK_MIN_CHARS,
        WORTH_MIN_CHARS,
        is_sparse_page_text,
        plan_worthiness_probe,
        should_probe_empty_study_range,
    )

    assert should_probe_empty_study_range(
        document_complete=True,
        newspaper=False,
        questions_generated=0,
        questions_answered=0,
        generation_pending=False,
    )
    assert not should_probe_empty_study_range(
        document_complete=False,
        newspaper=False,
        questions_generated=0,
        questions_answered=0,
        generation_pending=False,
    )
    assert is_sparse_page_text("x" * (WORTH_MIN_CHARS - 1))
    assert not is_sparse_page_text("x" * WORTH_MIN_CHARS)
    from app.services.content_worthiness import (
        IMPORT_TRANSCRIPT_MIN_CHARS,
        evaluate_reference_extract,
        is_import_extract_too_short,
    )

    assert is_import_extract_too_short("x" * (IMPORT_TRANSCRIPT_MIN_CHARS - 1))
    assert not is_import_extract_too_short("x" * IMPORT_TRANSCRIPT_MIN_CHARS)
    assert evaluate_reference_extract("x" * WORTH_MIN_CHARS)
    assert not evaluate_reference_extract("x" * (WORTH_MIN_CHARS - 1))
    assert not evaluate_reference_extract("Foo may refer to several topics in history.")
    assert not evaluate_reference_extract("Bar can refer to more than one historical topic.")
    from app.services.content_worthiness import (
        plan_stored_page_count_trust,
        should_trust_stored_page_count,
    )

    assert should_trust_stored_page_count(size_bytes=1000, stored_pages=1)
    assert not should_trust_stored_page_count(size_bytes=1000, stored_pages=8)
    assert not should_trust_stored_page_count(size_bytes=8000, stored_pages=1)
    multi = plan_stored_page_count_trust(size_bytes=1000, stored_pages=8)
    assert multi.trust and multi.pages == 8 and multi.reason == "multi_page"
    tiny = plan_stored_page_count_trust(size_bytes=1000, stored_pages=1)
    assert tiny.trust and tiny.pages == 1 and tiny.reason == "tiny_one_page"
    recount = plan_stored_page_count_trust(size_bytes=8000, stored_pages=1)
    assert not recount.trust and recount.reason == "recount"
    from app.services.content_worthiness import (
        STUDY_PAGE_CHARS,
        plan_study_page_split,
        should_break_on_section_heading,
        should_split_native_unit,
    )

    split = plan_study_page_split()
    assert split.chars_per_page == STUDY_PAGE_CHARS == 3200
    assert should_split_native_unit("x" * (split.native_split_chars + 1))
    assert not should_split_native_unit("x" * split.native_split_chars)
    assert should_break_on_section_heading(
        current_len=split.heading_break_min_chars, looks_like_heading=True
    )
    assert not should_break_on_section_heading(current_len=10, looks_like_heading=True)
    from app.services.content_worthiness import (
        NEWSPAPER_RELEVANCE_MAX_CHARS,
        OCR_BLANK_TRUST_TTL_SECONDS,
        VISION_OCR_TTL_SECONDS,
        plan_newspaper_relevance_input,
        plan_vision_ocr_cache,
        should_revalidate_ocr_blank,
    )

    clipped = plan_newspaper_relevance_input("x" * (NEWSPAPER_RELEVANCE_MAX_CHARS + 50))
    assert len(clipped) == NEWSPAPER_RELEVANCE_MAX_CHARS
    ocr = plan_vision_ocr_cache()
    assert ocr.transcription_ttl_seconds == VISION_OCR_TTL_SECONDS
    assert ocr.blank_trust_ttl_seconds == OCR_BLANK_TRUST_TTL_SECONDS
    assert should_revalidate_ocr_blank(
        hit="BLANK", requested_ttl_seconds=VISION_OCR_TTL_SECONDS
    )
    assert not should_revalidate_ocr_blank(
        hit="BLANK", requested_ttl_seconds=OCR_BLANK_TRUST_TTL_SECONDS
    )
    assert not should_revalidate_ocr_blank(
        hit="hello", requested_ttl_seconds=VISION_OCR_TTL_SECONDS
    )
    empty_probe = plan_worthiness_probe("empty_page")
    assert empty_probe.empty and empty_probe.min_chars == WORTH_MIN_CHARS
    pre = plan_worthiness_probe("pre_llm", has_text=True)
    assert not pre.empty and pre.check_junk and pre.min_chars == JUNK_MIN_CHARS
    from app.services.content_worthiness import should_heuristic_fallback_empty_aspects

    assert should_heuristic_fallback_empty_aspects(allow_zero=False, aspect_count=0)
    assert not should_heuristic_fallback_empty_aspects(allow_zero=True, aspect_count=0)
    assert not should_heuristic_fallback_empty_aspects(allow_zero=False, aspect_count=3)


def test_document_complete_prep_ready_and_newspaper_cook_target() -> None:
    from app.services.session_design import (
        NewspaperPageCookSignal,
        evaluate_document_complete,
        evaluate_page_prep_ready,
        pick_interview_coding_fallback,
        plan_newspaper_cook_target,
    )

    assert evaluate_document_complete(
        page_complete=True, on_last_page=True, newspaper=False, generation_pending=True
    )
    assert not evaluate_document_complete(
        page_complete=True, on_last_page=True, newspaper=True, generation_pending=True
    )
    assert evaluate_page_prep_ready(
        has_coverage=True, non_content=False, budget=6, generated=6, coverage_complete=False
    )
    assert evaluate_page_prep_ready(
        has_coverage=True, non_content=False, budget=6, generated=1, coverage_complete=True
    )
    learn_page = NewspaperPageCookSignal(
        page=2,
        has_active_generate=False,
        has_coverage=True,
        non_content=False,
        learn_budget=4,
        learn_generated=1,
        coverage_complete=True,
        test_budget=6,
        test_generated=0,
    )
    test_page = NewspaperPageCookSignal(
        page=3,
        has_active_generate=False,
        has_coverage=True,
        non_content=False,
        learn_budget=4,
        learn_generated=4,
        coverage_complete=True,
        test_budget=6,
        test_generated=0,
    )
    target = plan_newspaper_cook_target([learn_page, test_page])
    assert target.action == "cook" and target.cook_mode == "learn" and target.page == 2
    test_only = plan_newspaper_cook_target([test_page])
    assert test_only.cook_mode == "test" and test_only.page == 3
    bank = [{"question": "a"}, {"question": "b"}]
    assert pick_interview_coding_fallback(0, bank)["question"] == "a"
    assert pick_interview_coding_fallback(3, bank)["question"] == "b"
