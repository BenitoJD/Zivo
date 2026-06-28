"""Heuristic gate ↔ generation prompt alignment.

The fast regex gate (mcq_heuristics) and the writer prompt must agree: every
style the prompt tells the model to produce has to survive the gate, or each
such draft is pure wasted generation. These tests pin that contract — both the
real rejections we keep AND the assertion–reason / no-cloze alignment fix.
"""

from __future__ import annotations

from typing import Any

from app.services.mcq_heuristics import has_fatal_heuristic_flaws, run_heuristic_checks
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


def test_cloze_blank_stem_is_still_rejected() -> None:
    # Cloze was removed from the writer prompt because the gate rejects blanks;
    # the gate behaviour itself is unchanged.
    mcq = {
        "question": "A catalyst lowers the ___ of a reaction.",
        "options": ["activation energy", "temperature", "concentration", "pressure"],
        "correct_index": 0,
    }
    assert "unfocused_stem" in _codes(mcq)


def test_negative_stem_is_fatal() -> None:
    mcq = {
        "question": "Which of the following is NOT a property of a catalyst?",
        "options": ["It is consumed", "It lowers activation energy", "It is regenerated", "It speeds the rate"],
        "correct_index": 0,
    }
    assert "negative_wording" in _codes(mcq)
    assert has_fatal_heuristic_flaws(run_heuristic_checks(mcq)) is True


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


def test_writer_prompt_matches_the_gate() -> None:
    system = DEFAULTS["mcq_page_generate_system"]
    # The explicit auto-reject checklist (the core of the speed fix) is present.
    assert "AUTO-REJECT" in system
    # Assertion–reason is still offered (kept; gate now accepts it)...
    assert "assertion" in system.lower()
    # ...but cloze is no longer requested (the gate rejects blanks).
    assert "cloze" not in system.lower()


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
