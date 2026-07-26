"""Heuristic gate ↔ generation prompt alignment.

The fast regex gate (mcq_heuristics) and the writer prompt must agree: every
style the prompt tells the model to produce has to survive the gate, or each
such draft is pure wasted generation. These tests pin that contract — both the
real rejections we keep AND the assertion–reason / no-cloze alignment fix.
"""

from __future__ import annotations

from typing import Any

from app.services.mcq_heuristics import (
    find_invented_entity_flaws,
    has_fatal_heuristic_flaws,
    is_all_statements_correct_combination,
    run_heuristic_checks,
)
from app.services.prompts import DEFAULTS

# The four standard assertion–reason judgement options, in canonical order.
AR_OPTIONS = [
    "Both A and R are true, and R correctly explains A",
    "Both A and R are true, but R does not explain A",
    "A is true but R is false",
    "A is false but R is true",
]


def _codes(mcq: dict[str, Any]) -> set[str]:
    return {f["code"] for f in run_heuristic_checks(mcq)}


def test_assertion_reason_stem_not_flagged_for_missing_question_mark() -> None:
    # A/R items legitimately end with the Reason claim (a period), not "?". The
    # writer is told to produce them, so the gate must not fatally reject them.
    ar = {
        "question": (
            "Assertion (A): A catalyst speeds up a reaction. "
            "Reason (R): It lowers the activation energy of the reaction."
        ),
        "options": AR_OPTIONS,
        "correct_index": 0,
    }
    flaws = run_heuristic_checks(ar)
    assert "unfocused_stem" not in {f["code"] for f in flaws}
    assert has_fatal_heuristic_flaws(flaws) is False


def test_plain_declarative_stem_without_question_mark_still_fails() -> None:
    # The A/R exemption must stay narrow: an ordinary statement (not A/R) that
    # forgets the "?" is still an unfocused stem.
    mcq = {
        "question": "Catalysts lower the activation energy of a reaction.",
        "options": ["True for all reactions", "Only for exothermic ones", "Never", "Only with heat"],
        "correct_index": 0,
    }
    assert "unfocused_stem" in _codes(mcq)


def test_cloze_single_blank_stem_passes() -> None:
    # Cloze (exactly one ___ blank) is a requested exam style; the gate accepts it.
    mcq = {
        "question": "A catalyst lowers the ___ of a reaction.",
        "options": ["activation energy", "temperature", "concentration", "pressure"],
        "correct_index": 0,
    }
    assert has_fatal_heuristic_flaws(run_heuristic_checks(mcq)) is False


def test_multiple_blanks_are_rejected() -> None:
    mcq = {
        "question": "A ___ lowers the ___ of a reaction.",
        "options": ["catalyst / activation energy", "enzyme / temperature", "solvent / pressure", "buffer / pH"],
        "correct_index": 0,
    }
    assert "unfocused_stem" in _codes(mcq)


def test_capitalized_negative_stem_passes() -> None:
    # The deliberate negative/exception style (capitalized NOT/EXCEPT) is a valid
    # exam format the writer is told to produce; the gate accepts it.
    mcq = {
        "question": "Which of the following is NOT a property of a catalyst?",
        "options": ["It is consumed", "It lowers activation energy", "It is regenerated", "It speeds the rate"],
        "correct_index": 0,
    }
    assert has_fatal_heuristic_flaws(run_heuristic_checks(mcq)) is False


def test_hidden_lowercase_negative_is_fatal() -> None:
    mcq = {
        "question": "Which of the following is not a property of a catalyst?",
        "options": ["It is consumed", "It lowers activation energy", "It is regenerated", "It speeds the rate"],
        "correct_index": 0,
    }
    assert "negative_wording" in _codes(mcq)
    assert has_fatal_heuristic_flaws(run_heuristic_checks(mcq)) is True


def test_statement_combination_item_passes() -> None:
    # UPSC-style statement item: numbered claims on their own lines, enumerated
    # combination options (never "All of the above").
    mcq = {
        "question": (
            "Consider the following statements:\n"
            "1. A catalyst lowers the activation energy of a reaction.\n"
            "2. A catalyst is consumed during the reaction.\n"
            "3. A catalyst does affect the position of equilibrium.\n"
            "Which of the statements given above is/are correct?"
        ),
        "options": ["1 only", "1 and 2 only", "2 and 3 only", "1, 2 and 3"],
        "correct_index": 0,
    }
    assert has_fatal_heuristic_flaws(run_heuristic_checks(mcq)) is False


def test_all_of_the_above_option_is_fatal() -> None:
    mcq = {
        "question": "What does a catalyst do to a reaction?",
        "options": ["Speeds it up", "Lowers activation energy", "Is regenerated", "All of the above"],
        "correct_index": 3,
    }
    assert "none_or_all_of_above" in _codes(mcq)


def test_dangling_reference_is_not_self_contained() -> None:
    mcq = {
        "question": "As shown in the figure above, which layer contains most chloroplasts?",
        "options": ["Palisade mesophyll", "Epidermis", "Xylem", "Cuticle"],
        "correct_index": 0,
    }
    assert "not_self_contained" in _codes(mcq)


def test_clean_standard_question_has_no_fatal_flaws() -> None:
    mcq = {
        "question": "Why does a catalyst speed up a reaction without being consumed?",
        "options": [
            "It lowers the activation energy so more collisions succeed",
            "It raises the temperature of the reactants directly",
            "It increases the concentration of the reactants",
            "It shifts the equilibrium toward the products",
        ],
        "correct_index": 0,
    }
    assert has_fatal_heuristic_flaws(run_heuristic_checks(mcq)) is False


def test_invented_exam_compound_rejected_when_not_in_source() -> None:
    page = (
        "Protesters chanted slogans targeting the ALPHA ethanol blending policy "
        "after the minister resigned."
    )
    mcq = {
        "question": (
            "Consider the following statements:\n"
            "1. The slogan targeted the ALPHA examination.\n"
            "Which of the statements given above is/are correct?"
        ),
        "options": ["1 only", "2 only", "1 and 2 only", "None"],
        "correct_index": 0,
    }
    flaws = find_invented_entity_flaws(mcq, page)
    assert flaws and flaws[0]["code"] == "invented_entity"
    assert has_fatal_heuristic_flaws(flaws) is True


def test_grounded_exam_phrase_passes_invented_entity_check() -> None:
    page = "Students protested demanding reforms to the NEET exam schedule."
    mcq = {
        "question": "What did students protest about regarding the NEET exam?",
        "options": ["Schedule", "Fees", "Syllabus", "Centres"],
        "correct_index": 0,
    }
    assert find_invented_entity_flaws(mcq, page) == []


def test_all_statements_correct_detected() -> None:
    mcq = {
        "question": (
            "Consider the following statements:\n"
            "1. First claim.\n"
            "2. Second claim.\n"
            "3. Third claim.\n"
            "Which of the statements given above is/are correct?"
        ),
        "options": ["1 only", "2 only", "1 and 3 only", "1, 2 and 3"],
        "correct_index": 3,
    }
    assert is_all_statements_correct_combination(mcq) is True


def test_second_all_statements_correct_on_page_rejected() -> None:
    prior = {
        "question": (
            "Consider the following statements:\n"
            "1. A.\n"
            "2. B.\n"
            "Which of the statements given above is/are correct?"
        ),
        "options": ["1 only", "2 only", "1 and 2 only"],
        "correct_index": 2,
    }
    mcq = {
        "question": (
            "Consider the following statements:\n"
            "1. X.\n"
            "2. Y.\n"
            "3. Z.\n"
            "Which of the statements given above is/are correct?"
        ),
        "options": ["1 only", "2 and 3 only", "1 and 3 only", "1, 2 and 3"],
        "correct_index": 3,
    }
    flaws = run_heuristic_checks(mcq, prior_mcqs=[prior])
    assert "all_statements_combination_bias" in {f["code"] for f in flaws}
    assert has_fatal_heuristic_flaws(flaws) is True


def test_writer_prompt_matches_the_gate() -> None:
    system = DEFAULTS["mcq_page_generate_system"]
    # The explicit auto-reject checklist (the core of the speed fix) is present.
    assert "AUTO-REJECT" in system
    # The full exam-style repertoire is offered, and the gate accepts each shape.
    lowered = system.lower()
    for style in ("assertion", "cloze", "statement", "match", "ordering", "negative", "numerical"):
        assert style in lowered, f"writer prompt no longer offers the {style} style"


def test_prompt_warns_about_every_avoidable_fatal_trigger() -> None:
    # Contract: the writer prompt must tell the model about each fatal pattern the
    # gate auto-rejects, or drafts get silently discarded (the gate<->prompt
    # contradiction that wasted ~80% of drafts). One assertion per gate rule.
    s = DEFAULTS["mcq_page_generate_system"].lower()
    assert "not" in s and "except" in s and "false" in s and "never" in s  # negative_wording
    assert "none of the above" in s and "all of the above" in s  # none_or_all_of_above
    assert "___" in s or "blank" in s  # unfocused_stem (fill-in-the-blank)
    assert "figure" in s and "above" in s  # not_self_contained (dangling refs)
    assert "page" in s and "book" in s  # meta_page_reference
    assert "longest" in s  # longest_option_correct
    assert "?" in DEFAULTS["mcq_page_generate_system"]  # stem-ends-with-? rule stated
    assert "named labels" in s  # invented_entity / domain remap rule in writer prompt
