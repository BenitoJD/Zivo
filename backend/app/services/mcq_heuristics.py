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
        # The marked answer is not the one the source independently supports — the
        # gravest flaw (it teaches the wrong thing). Set by the answer-key verifier.
        "wrong_answer_key",
        "invalid_structure",
        "too_similar_to_prior",
        "meta_page_reference",
        "not_self_contained",
        "invented_entity",
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
# A deliberate negative/exception item is a valid exam format ONLY when the
# negative word is capitalized so it cannot be missed (NOT / EXCEPT / LEAST).
# Hidden lowercase negatives remain a fatal flaw.
_CAPITALIZED_NEGATIVE_RE = re.compile(r"\b(NOT|EXCEPT|LEAST|FALSE|INCORRECT)\b")
_BLANK_RE = re.compile(r"_{3,}")
_ELLIPSIS_OR_BRACKET_BLANK_RE = re.compile(r"\.{3,}\s*$|\[\s*\]")
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
# Compound labels invented by attaching exam/scheme words to a code from the article.
_INVENTED_LABEL_RE = re.compile(
    r"\b([A-Za-z0-9][\w-]{0,24})\s+"
    r"(examinations?|exams?|schemes?|yojanas?|missions?|programs?|programmes?)\b",
    re.IGNORECASE,
)


def _norm_compact(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _label_suffix_in_source(page_text: str, label: str, suffix: str, window: int = 48) -> bool:
    """True when label and suffix appear near each other in the source."""
    page_lower = page_text.lower()
    label_lower = label.lower()
    suffix_lower = suffix.lower()
    start = 0
    while True:
        idx = page_lower.find(label_lower, start)
        if idx == -1:
            return False
        snippet = page_lower[idx : idx + len(label_lower) + window]
        if suffix_lower in snippet:
            return True
        start = idx + 1


def find_invented_entity_flaws(
    mcq: dict[str, Any],
    page_text: str,
) -> list[dict[str, str]]:
    """Reject exam/scheme compounds not grounded in the page text."""
    if not (page_text or "").strip():
        return []
    combined = "\n".join(
        [
            str(mcq.get("question") or mcq.get("stem") or ""),
            *[str(o) for o in (mcq.get("options") or mcq.get("choices") or [])],
        ]
    )
    page_norm = _norm_compact(page_text)
    for match in _INVENTED_LABEL_RE.finditer(combined):
        full = match.group(0)
        full_norm = _norm_compact(full)
        if full_norm in page_norm:
            continue
        label = match.group(1)
        suffix = match.group(2)
        if _label_suffix_in_source(page_text, label, suffix):
            continue
        return [
            {
                "code": "invented_entity",
                "message": (
                    f"Label '{full}' is not used in the source — "
                    "do not attach exam/scheme words to acronyms the article does not combine"
                ),
            }
        ]
    return []


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

    # Multi-select ("select all that apply") items key on correct_indices; validate
    # the set and remember it so single-answer-only checks (longest option) skip.
    raw_multi = mcq.get("correct_indices")
    multi_indices: list[int] | None = None
    if isinstance(raw_multi, list) and len(raw_multi) >= 2:
        try:
            cand = sorted({int(i) for i in raw_multi})
        except (TypeError, ValueError):
            flaws.append({"code": "invalid_structure", "message": "correct_indices invalid"})
            return flaws
        if any(i < 0 or i >= len(options) for i in cand):
            flaws.append({"code": "invalid_structure", "message": "correct_indices out of range"})
            return flaws
        multi_indices = cand

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

    # Cloze (exactly one ___ blank) is a valid exam format; multiple blanks,
    # trailing ellipses, and empty brackets remain flaws.
    blanks = _BLANK_RE.findall(question)
    if len(blanks) > 1 or _ELLIPSIS_OR_BRACKET_BLANK_RE.search(question):
        flaws.append({"code": "unfocused_stem", "message": "Malformed blank (multiple ___, trailing …, or [ ])"})

    if _NEGATIVE_STEM_RE.search(question) and not _CAPITALIZED_NEGATIVE_RE.search(question):
        flaws.append({"code": "negative_wording", "message": "Hidden lowercase negative in stem (capitalize NOT/EXCEPT or rephrase)"})

    # The "longest option is the answer" give-away only makes sense for a single
    # correct option — skip it for multi-select, where several options are correct.
    if multi_indices is None:
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

    if not question.endswith("?") and not _is_assertion_reason(question) and len(blanks) != 1:
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
