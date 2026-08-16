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
    s = plan_session(100, soft_cap=20, mode="learn")
    assert s.n_session == 20
    t = plan_session(100, soft_cap=20, mode="test")
    assert t.n_session == 25


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
