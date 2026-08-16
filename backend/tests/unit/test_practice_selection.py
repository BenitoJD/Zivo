"""Practice Selection Engine — overlap×difficulty + attempt bias."""

from __future__ import annotations

from app.services.practice_selection import (
    PRACTICE_SEL_VERSION,
    PracticeCandidate,
    pick_next,
    score_candidate,
)


def test_score_overlap_and_difficulty() -> None:
    c = PracticeCandidate(id="1", concept_keys=("arrays",), difficulty="hard")
    assert score_candidate(c, ["arrays"]) == 10 + 3


def test_attempt_bias_prefers_unattempted() -> None:
    cands = [
        PracticeCandidate(id="done", concept_keys=("cap",), difficulty="hard"),
        PracticeCandidate(id="fresh", concept_keys=("cap",), difficulty="easy"),
    ]
    pick = pick_next(cands, ["cap"], attempted_ids=["done"])
    assert pick.id == "fresh"
    assert pick.policy_version == PRACTICE_SEL_VERSION


def test_no_attempt_ids_skips_bias() -> None:
    hard = PracticeCandidate(id="h", concept_keys=("x",), difficulty="hard")
    easy = PracticeCandidate(id="e", concept_keys=("x",), difficulty="easy")
    # Without attempted_ids, higher difficulty wins even if "attempted" in real life.
    pick = pick_next([easy, hard], ["x"])
    assert pick.id == "h"
    assert score_candidate(hard, ["x"], attempted=True) < score_candidate(
        hard, ["x"], attempted=False
    )
    from app.services.practice_selection import plan_coding_next_pool_limit

    assert plan_coding_next_pool_limit() == 80
