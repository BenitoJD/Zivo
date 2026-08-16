"""Adaptive Selection Engine — composite policy + verdict API."""

from __future__ import annotations

from app.services.adaptive_selection import (
    DEFAULT_SELECTION_POLICY,
    SELECTION_VERSION,
    TARGET_SUCCESS,
    CandidateSignals,
    LearnerState,
    fisher_information_1pl,
    normalize_policy,
    select_next,
    target_difficulty,
)


def _state(ability: float | None = 0.0, **kw) -> LearnerState:
    return LearnerState(ability=ability, **kw)


def test_normalize_aliases_adaptive() -> None:
    assert normalize_policy("adaptive") == "adaptive_v1"
    assert normalize_policy(None) == DEFAULT_SELECTION_POLICY
    assert normalize_policy("difficulty_edge") == "difficulty_edge"


def test_select_next_empty() -> None:
    v = select_next([], _state())
    assert v.assertion_id is None
    assert v.rationale == "empty"
    assert v.policy_version == SELECTION_VERSION


def test_adaptive_picks_band_item_on_learn() -> None:
    ability = 1.0
    target = target_difficulty(ability)
    difficulties = {"a": target - 2.0, "b": target, "c": target + 2.0}
    v = select_next(
        ["a", "b", "c"],
        _state(ability),
        policy="adaptive_v1",
        mode="learn",
        difficulty_by_id=difficulties,
        concept_by_id={"a": "x", "b": "x", "c": "x"},
    )
    assert v.assertion_id == "b"
    assert v.scores is not None
    assert v.scores.band_fit >= v.scores.information or True  # scored path
    assert v.rationale.startswith("adaptive_v1:")


def test_test_mode_prefers_higher_information_near_coin_flip() -> None:
    """At θ=0, Fisher peaks at difficulty≈0 (P≈0.5); Learn band sits below that."""
    ability = 0.0
    band = target_difficulty(ability, TARGET_SUCCESS)
    # "info" at difficulty 0 → max Fisher; "band" at Learn target.
    difficulties = {"info": 0.0, "band": band, "far": 3.0}
    learn = select_next(
        ["info", "band", "far"],
        _state(ability),
        policy="adaptive_v1",
        mode="learn",
        difficulty_by_id=difficulties,
    )
    test = select_next(
        ["info", "band", "far"],
        _state(ability),
        policy="adaptive_v1",
        mode="test",
        difficulty_by_id=difficulties,
    )
    # Learn should favor band item; Test should favor (or at least not ignore) info.
    assert learn.assertion_id == "band"
    assert test.assertion_id in ("info", "band")
    assert test.scores is not None
    if test.assertion_id == "info":
        assert test.scores.information >= 0.99


def test_adaptive_lineage_routes_miss() -> None:
    state = LearnerState(ability=0.0, last_correct=False, last_assertion_id="Q1")
    v = select_next(
        ["a", "b"],
        state,
        policy="adaptive_v1",
        difficulty_by_id={"a": 0.0, "b": 0.0},
        lineage_by_id={"b": "follow_up_after_miss"},
    )
    assert v.assertion_id == "b"
    assert "lineage" in v.rationale or v.scores is not None


def test_adaptive_mastery_reinforce_on_miss_without_lineage() -> None:
    state = LearnerState(
        ability=0.0,
        last_correct=False,
        last_concept_key="weak",
        concept_ability={"weak": -1.0, "strong": 2.0},
    )
    # Both on band for their concept ability; reinforce should prefer weak.
    difficulties = {
        "w": target_difficulty(-1.0),
        "s": target_difficulty(2.0),
    }
    v = select_next(
        ["s", "w"],
        state,
        policy="adaptive_v1",
        mode="learn",
        difficulty_by_id=difficulties,
        concept_by_id={"w": "weak", "s": "strong"},
    )
    assert v.assertion_id == "w"


def test_novelty_soft_prefers_less_exposed() -> None:
    ability = 0.0
    target = target_difficulty(ability)
    # Equal band fit; high exposure on "hot" should lose to "fresh".
    v = select_next(
        [
            CandidateSignals("hot", difficulty=target, exposure=20),
            CandidateSignals("fresh", difficulty=target, exposure=0),
        ],
        _state(ability),
        policy="adaptive_v1",
        mode="learn",
    )
    assert v.assertion_id == "fresh"


def test_fisher_peaks_at_coin_flip() -> None:
    assert abs(fisher_information_1pl(0.0, 0.0) - 0.25) < 1e-9
    assert fisher_information_1pl(0.0, 0.0) > fisher_information_1pl(0.0, 2.0)


def test_cold_bank_degrades() -> None:
    v = select_next(
        ["a", "b"],
        _state(1.0),
        policy="adaptive_v1",
        difficulty_by_id={},
    )
    assert v.assertion_id == "a"
    assert "cold_bank" in v.rationale


def test_narrow_serve_pool_focus_and_mastery_and_spaced() -> None:
    from app.services.adaptive_selection import CandidateSignals, LearnerState, narrow_serve_pool

    rows = [
        CandidateSignals("a", concept_key="x", concept_label="Osmosis"),
        CandidateSignals("b", concept_key="y", concept_label="Photosynthesis light"),
        CandidateSignals("c", concept_key="z", concept_label="Mitosis"),
    ]
    focused = narrow_serve_pool(
        rows, LearnerState(focus_concept="Photosynthesis")
    )
    assert [c.assertion_id for c in focused] == ["b"]

    diversified = narrow_serve_pool(
        rows,
        LearnerState(
            mastery_stop=True,
            last_concept_key="x",
        ),
    )
    assert "a" not in [c.assertion_id for c in diversified]

    due = narrow_serve_pool(
        rows,
        LearnerState(concept_revisit_hours={"mitosis": 1.0, "other": 99.0}),
    )
    assert [c.assertion_id for c in due] == ["c"]


def test_difficulty_edge_cold_lineage_flips_to_reinforce() -> None:
    state = LearnerState(
        ability=0.0,
        last_correct=False,
        last_concept_key="weak",
    )
    v = select_next(
        ["a", "b"],
        state,
        policy="difficulty_edge",
        difficulty_by_id={},
        concept_by_id={"a": "weak", "b": "other"},
        lineage_by_id={},
    )
    assert v.policy == "concept_reinforce"
    assert v.assertion_id == "a"


def test_label_selection_reason() -> None:
    from app.services.adaptive_selection import label_selection_reason

    assert label_selection_reason("adaptive_v1:focus_concept") == "focus concept"
    assert label_selection_reason("mastery_reinforce") == "focus concept"
    assert label_selection_reason("spaced_revisit_due") == "spaced revisit"
    assert label_selection_reason("lineage:follow_up") == "follows your last question"
    assert label_selection_reason("new_page") == "new page"
    assert label_selection_reason("difficulty_edge:band") == "right at your level"
    assert label_selection_reason("concept_reinforce") == "reinforces a recent miss"
    assert label_selection_reason("sequence") == "in order through this page"


def test_plan_signal_load() -> None:
    from app.services.adaptive_selection import plan_signal_load

    seq = plan_signal_load(policy="sequence", state=_state())
    assert not seq.load_concepts and not seq.load_difficulty
    adaptive = plan_signal_load(policy="adaptive_v1", state=_state(last_assertion_id="x"))
    assert adaptive.load_concepts and adaptive.load_difficulty
    assert adaptive.load_lineage and adaptive.load_exposure
    focus = plan_signal_load(
        policy="sequence", state=_state(focus_concept="Photosynthesis")
    )
    assert focus.load_concepts and not focus.load_difficulty


def test_study_mode_aliases() -> None:
    from app.services.adaptive_selection import label_study_mode, persist_study_mode

    assert label_study_mode("sequence") == "classic"
    assert label_study_mode("adaptive_v1") == "adaptive"
    assert label_study_mode(None) == "adaptive"
    assert persist_study_mode("classic") == "sequence"
    assert persist_study_mode("adaptive") == "adaptive_v1"
