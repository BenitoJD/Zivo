"""MCQ heuristic quality gate (pure, regex/string-based — no LLM/DB).

Extracted from mcq_quality.py: fast structural checks that catch obviously-flawed
items (negative stems, fill-in-the-blank, all-of-the-above, non-self-contained
stems, absolutes, document-meta references), plus FATAL_FLAW_CODES — the subset
that hard-fails an item. Leaf module: the critic/generation pipeline imports from
here, not vice versa.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.mcq_dedup import has_document_meta_reference, stems_match

FATAL_FLAW_CODES = frozenset(
    {
        # Gate A — the question must force thinking, not recognition. A stem whose
        # answer can be keyword-matched or recalled as a rote phrase is a flaw now,
        # not a pass: it fails the testing-effect bar (recall builds memory,
        # recognition does not).
        "recognition_only",
        "ambiguous_unclear",
        "more_than_one_correct",
        "implausible_distractors",
        "none_or_all_of_above",
        "unfocused_stem",
        "longest_option_correct",
        "negative_wording",
        "not_grounded",
        "invalid_structure",
        "too_similar_to_prior",
        "meta_page_reference",
        "not_self_contained",
    }
)

_NONE_ALL_RE = re.compile(
    r"\b(none of the above|all of the above|both a and b|a and b are correct)\b",
    re.IGNORECASE,
)
_NEGATIVE_STEM_RE = re.compile(
    r"\b(which .{0,40} not\b|except\b|least likely\b|incorrect\b|false\b|never\b.{0,20}\?)",
    re.IGNORECASE,
)
_FILL_BLANK_RE = re.compile(r"_{3,}|\.{3,}\s*$|\[\s*\]")
# Dangling references — the stem/option points at something the reader can't see
# (a figure, the text above, an example, an "aforementioned" noun). A question
# with these can't stand alone outside the source document.
_NOT_SELF_CONTAINED_RE = re.compile(
    r"\b(?:"
    r"the\s+above(?:[-\s]mentioned)?"
    r"|the\s+aforementioned"
    r"|as\s+(?:shown|depicted|illustrated|described|mentioned|discussed|stated)(?:\s+(?:above|earlier|previously|in\s+the\s+(?:figure|diagram|table|example|text|passage)))?"
    r"|in\s+the\s+(?:figure|diagram|table|chart|image|example|illustration|passage|reading|excerpt|text|case)\s*(?:above|below|shown|depicted)?"
    r"|this\s+(?:figure|diagram|table|chart|image|example|illustration|passage|reading|excerpt|text|case|section|chapter)"
    r"|the\s+(?:figure|diagram|table|chart|image|illustration)\s+(?:above|below|shown|depicted|illustrating)"
    r"|refer\s+to\s+the\s+(?:figure|diagram|table|chart|image|example|text|passage)"
    r"|see\s+(?:figure|diagram|table|chart|image|example|above|below)"
    r"|given\s+(?:text|passage|reading|excerpt|example|case|scenario)"
    r"|from\s+the\s+(?:above|aforementioned|preceding|previous|given)\s+(?:text|passage|reading|excerpt|example|case|scenario|discussion)"
    r")\b",
    re.IGNORECASE,
)
_ABSOLUTE_RE = re.compile(r"\b(always|never|only|all|none)\b", re.IGNORECASE)
# Assertion–Reason items are clear, structured stems that legitimately end with a
# period (the Reason claim), not "?". Recognize the canonical shape so the
# question-mark rule doesn't fatally reject a valid A/R item — the writer is told
# to produce these, so rejecting them was pure wasted generation.
_ASSERTION_REASON_RE = re.compile(r"\bassertion\b.*\breason\b", re.IGNORECASE | re.DOTALL)


def _is_assertion_reason(question: str) -> bool:
    return bool(_ASSERTION_REASON_RE.search(question))


def run_heuristic_checks(
    mcq: dict[str, Any],
    *,
    prior_mcqs: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Fast rule-based IWF checks before the LLM critic."""
    flaws: list[dict[str, str]] = []

    question = (mcq.get("question") or mcq.get("stem") or "").strip()
    options = [str(o).strip() for o in (mcq.get("options") or mcq.get("choices") or []) if str(o).strip()]
    correct_index = mcq.get("correct_index")

    if not question or len(options) < 2:
        flaws.append({"code": "invalid_structure", "message": "Question or options missing"})
        return flaws

    try:
        ci = int(correct_index)
    except (TypeError, ValueError):
        flaws.append({"code": "invalid_structure", "message": "correct_index invalid"})
        return flaws

    if ci < 0 or ci >= len(options):
        flaws.append({"code": "invalid_structure", "message": "correct_index out of range"})
        return flaws

    lowered = [o.lower() for o in options]
    if len(set(lowered)) != len(lowered):
        flaws.append({"code": "ambiguous_unclear", "message": "Duplicate answer options"})

    for opt in options:
        if _NONE_ALL_RE.search(opt):
            flaws.append({"code": "none_or_all_of_above", "message": "Option uses none/all of the above"})
            break

    if _NONE_ALL_RE.search(question):
        flaws.append({"code": "none_or_all_of_above", "message": "Stem references none/all of the above"})

    if has_document_meta_reference(question):
        flaws.append(
            {
                "code": "meta_page_reference",
                "message": "Stem uses exam-forbidden book/page/passage framing instead of asking the concept directly",
            }
        )

    for opt in options:
        if has_document_meta_reference(opt):
            flaws.append(
                {
                    "code": "meta_page_reference",
                    "message": "Option references a book, page, passage, or document",
                }
            )
            break

    # Self-contained check — the question must be answerable without the source.
    if _NOT_SELF_CONTAINED_RE.search(question):
        flaws.append(
            {
                "code": "not_self_contained",
                "message": "Stem dangles a reference (figure/above/example/text) only visible in the source",
            }
        )
    for opt in options:
        if _NOT_SELF_CONTAINED_RE.search(opt):
            flaws.append(
                {
                    "code": "not_self_contained",
                    "message": "Option dangles a reference only visible in the source",
                }
            )
            break

    if _FILL_BLANK_RE.search(question):
        flaws.append({"code": "unfocused_stem", "message": "Fill-in-the-blank style stem"})

    if _NEGATIVE_STEM_RE.search(question):
        flaws.append({"code": "negative_wording", "message": "Negative or exception-style stem"})

    correct_len = len(options[ci])
    other_lens = [len(o) for i, o in enumerate(options) if i != ci]
    if other_lens:
        avg_other = sum(other_lens) / len(other_lens)
        if avg_other > 0 and correct_len > avg_other * 1.6 and correct_len - avg_other > 12:
            flaws.append(
                {
                    "code": "longest_option_correct",
                    "message": "Correct option noticeably longer than distractors",
                }
            )

    if _ABSOLUTE_RE.search(options[ci]):
        for i, opt in enumerate(options):
            if i != ci and _ABSOLUTE_RE.search(opt):
                flaws.append(
                    {
                        "code": "grammatical_cues",
                        "message": "Absolute terms appear on multiple options",
                    }
                )
                break

    if not question.endswith("?") and not _is_assertion_reason(question):
        flaws.append({"code": "unfocused_stem", "message": "Stem should be a clear question ending with ?"})

    if prior_mcqs:
        for prior in prior_mcqs:
            if stems_match(question, str(prior.get("question") or "")):
                flaws.append(
                    {
                        "code": "too_similar_to_prior",
                        "message": "Stem matches a prior question on this page",
                    }
                )
                break

    return flaws


def has_fatal_heuristic_flaws(flaws: list[dict[str, str]]) -> bool:
    return any(f.get("code") in FATAL_FLAW_CODES for f in flaws)
