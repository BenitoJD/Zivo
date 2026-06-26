"""Open-world tutor loop — askability gate, content-aware style, selection policy.

Pure-logic coverage (no DB): the parts that decide whether to ask anything, how
to frame it, and which question comes next given the last answer.
"""

from app.graphs.page_triage_graph import _looks_like_junk, _non_content_result
from app.services.prompts import content_type_style
from app.services.selection import LearnerState, build_learner_state, choose_next_assertion


# --- askability gate ---------------------------------------------------------

def test_junk_gate_flags_empty_and_noise() -> None:
    assert _looks_like_junk("") is True
    assert _looks_like_junk("   ") is True
    assert _looks_like_junk("....,,,,;;;; 123 456 789 %%%%") is True  # mostly non-letters
    assert _looks_like_junk("Table 1 2 3 4 5 6 7 8 9 10 11 12") is True  # too few words


def test_junk_gate_passes_real_prose() -> None:
    text = (
        "Photosynthesis converts light energy into chemical energy stored in glucose. "
        "Chlorophyll in the chloroplast absorbs photons to drive the reaction."
    )
    assert _looks_like_junk(text) is False


def test_non_content_result_is_a_deliberate_zero() -> None:
    r = _non_content_result(content_type="non_content", rationale="Cover page.")
    assert r["question_budget"] == 0
    assert r["aspects"] == []
    assert r["non_content"] is True


# --- content-aware framing ---------------------------------------------------

def test_content_type_style_known_and_unknown() -> None:
    assert "interpretation" in content_type_style("narrative").lower()
    assert content_type_style("expository") != ""
    assert content_type_style(None) == ""
    assert content_type_style("nonsense-type") == ""


# --- selection policy --------------------------------------------------------

def _candidates():
    # id -> concept
    return ["q1", "q2", "q3"], {"q1": "alpha", "q2": "alpha", "q3": "beta"}


def test_sequence_policy_returns_first() -> None:
    cands, concepts = _candidates()
    state = LearnerState(last_concept_key="beta", last_correct=False)
    assert choose_next_assertion("sequence", cands, concepts, state) == "q1"


def test_reinforce_repeats_concept_after_wrong_answer() -> None:
    # Last answer was on 'beta' and WRONG → prefer the next 'beta' question.
    cands = ["q1", "q3"]
    concepts = {"q1": "alpha", "q3": "beta"}
    state = LearnerState(last_concept_key="beta", last_correct=False)
    assert choose_next_assertion("concept_reinforce", cands, concepts, state) == "q3"


def test_reinforce_advances_to_new_concept_after_right_answer() -> None:
    # Last answer was on 'alpha' and RIGHT → move on to a different concept.
    cands, concepts = _candidates()
    state = LearnerState(last_concept_key="alpha", last_correct=True)
    assert choose_next_assertion("concept_reinforce", cands, concepts, state) == "q3"


def test_reinforce_falls_back_to_sequence_without_signal() -> None:
    cands, concepts = _candidates()
    state = LearnerState(last_concept_key=None, last_correct=None)
    assert choose_next_assertion("concept_reinforce", cands, concepts, state) == "q1"


def test_reinforce_never_returns_outside_candidates() -> None:
    # Wrong on a concept that has no remaining questions → safe sequence fallback.
    cands = ["q1", "q2"]
    concepts = {"q1": "alpha", "q2": "alpha"}
    state = LearnerState(last_concept_key="gamma", last_correct=False)
    assert choose_next_assertion("concept_reinforce", cands, concepts, state) in cands


def test_build_learner_state_reads_progress() -> None:
    progress = {
        "last_confirmed_answer": {"concept_key": "alpha", "correct": False, "choice_index": 2}
    }
    state = build_learner_state(progress)
    assert state.last_concept_key == "alpha"
    assert state.last_correct is False
    # Empty progress yields a no-signal state.
    assert build_learner_state({}).last_concept_key is None
