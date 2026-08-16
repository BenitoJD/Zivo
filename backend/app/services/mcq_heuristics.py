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

from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.services.mcq_dedup import has_document_meta_reference, stems_match
from app.services.presence import evaluate_presence

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
        "all_statements_combination_bias",
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

_STRUCT_RULES = (
    Rule(when=(Pred("no_q_or_opts", "truthy"),), action="missing"),
    Rule(when=(Pred("multi_invalid", "truthy"),), action="multi_invalid"),
    Rule(when=(Pred("multi_range", "truthy"),), action="multi_range"),
    Rule(when=(Pred("ci_invalid", "truthy"),), action="ci_invalid"),
    Rule(when=(Pred("ci_range", "truthy"),), action="ci_range"),
    Rule(when=(), action="ok"),
)

_COMBO_RULES = (
    Rule(when=(Pred("not_statement", "truthy"),), action="no"),
    Rule(when=(Pred("too_few", "truthy"),), action="no"),
    Rule(when=(Pred("ci_invalid", "truthy"),), action="no"),
    Rule(when=(Pred("ci_range", "truthy"),), action="no"),
    Rule(when=(Pred("all_keyed", "truthy"),), action="yes"),
    Rule(when=(), action="no"),
)

_INVENTED_RULES = (
    Rule(when=(Pred("empty_page", "truthy"),), action="none"),
    Rule(when=(Pred("has_hit", "truthy"),), action="hit"),
    Rule(when=(), action="none"),
)


def _norm_compact(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _label_suffix_in_source(page_text: str, label: str, suffix: str, window: int = 48) -> bool:
    """True when label and suffix appear near each other in the source."""
    page_lower = page_text.lower()
    label_lower = label.lower()
    suffix_lower = suffix.lower()

    def find_from(start: int) -> bool:
        idx = page_lower.find(label_lower, start)

        def at_idx() -> bool:
            snippet = page_lower[idx : idx + len(label_lower) + window]
            return pick(
                suffix_lower in snippet,
                lambda: True,
                lambda: find_from(idx + 1),
            )

        return pick(idx == -1, lambda: False, at_idx)

    return find_from(0)


def _invented_match_flaw(match: re.Match[str], page_text: str, page_norm: str) -> dict[str, str] | None:
    full = match.group(0)
    full_norm = _norm_compact(full)
    return pick(
        full_norm in page_norm,
        lambda: None,
        lambda: pick(
            _label_suffix_in_source(page_text, match.group(1), match.group(2)),
            lambda: None,
            lambda: {
                "code": "invented_entity",
                "message": (
                    f"Label '{full}' is not used in the source — "
                    "do not attach exam/scheme words to acronyms the article does not combine"
                ),
            },
        ),
    )


def find_invented_entity_flaws(
    mcq: dict[str, Any],
    page_text: str,
) -> list[dict[str, str]]:
    """Reject exam/scheme compounds not grounded in the page text."""
    combined = "\n".join(
        [
            str(mcq.get("question") or mcq.get("stem") or ""),
            *[str(o) for o in (mcq.get("options") or mcq.get("choices") or [])],
        ]
    )
    page_norm = _norm_compact(page_text)
    hit = next(
        filter(
            None,
            map(
                lambda m: _invented_match_flaw(m, page_text, page_norm),
                _INVENTED_LABEL_RE.finditer(combined),
            ),
        ),
        None,
    )
    rule = first_match(
        _INVENTED_RULES,
        {
            "empty_page": evaluate_presence((page_text or "").strip()).action != "ok",
            "has_hit": hit is not None,
        },
    )
    return apply(rule.action, {"none": lambda: [], "hit": lambda: [hit]})


_STATEMENT_STEM_RE = re.compile(r"consider the following statements", re.IGNORECASE)
_STMT_NUM_RE = re.compile(r"(?:^|\n)\s*(\d+)\.\s", re.MULTILINE)


def _statement_numbers_in_stem(question: str) -> list[int]:
    return sorted({int(m.group(1)) for m in _STMT_NUM_RE.finditer(question)})


def _numbers_in_text(text: str) -> set[int]:
    return {int(x) for x in re.findall(r"\d+", text)}


def is_all_statements_correct_combination(mcq: dict[str, Any]) -> bool:
    """True when a statement-based item keys every numbered statement as correct."""
    question = str(mcq.get("question") or mcq.get("stem") or "").strip()
    stmt_nums = _statement_numbers_in_stem(question)
    options = mcq.get("options") or mcq.get("choices") or []
    try:
        ci = int(mcq.get("correct_index", 0))
        ci_invalid = False
    except (TypeError, ValueError):
        ci = 0
        ci_invalid = True
    chosen = pick(
        not ci_invalid and 0 <= ci < len(options),
        lambda: _numbers_in_text(str(options[ci])),
        lambda: set(),
    )
    hit = first_match(
        _COMBO_RULES,
        {
            "not_statement": not _STATEMENT_STEM_RE.search(question),
            "too_few": len(stmt_nums) < 2,
            "ci_invalid": ci_invalid,
            "ci_range": not (0 <= ci < len(options)),
            "all_keyed": set(stmt_nums) == chosen and len(chosen) == len(stmt_nums),
        },
    )
    return hit.action == "yes"


def find_all_statements_combination_bias_flaws(
    mcq: dict[str, Any],
    prior_mcqs: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Reject a second 'all statements correct' combo on the same page."""
    priors = prior_mcqs or []
    prior_all_correct = sum(
        1 for _p in filter(is_all_statements_correct_combination, priors)
    )
    return pick(
        not is_all_statements_correct_combination(mcq),
        lambda: [],
        lambda: pick(
            prior_all_correct >= 1,
            lambda: [
                {
                    "code": "all_statements_combination_bias",
                    "message": (
                        "This page already has an all-statements-correct item — "
                        "key a different combination (1 only, 2 only, 1 and 3 only, etc.)"
                    ),
                }
            ],
            lambda: [],
        ),
    )


def _is_assertion_reason(question: str) -> bool:
    return bool(_ASSERTION_REASON_RE.search(question))


def _parse_multi_indices(
    raw_multi: Any, n_options: int
) -> tuple[list[int] | None, bool, bool]:
    """Return (indices_or_none, invalid, out_of_range)."""

    def parsed() -> tuple[list[int] | None, bool, bool]:
        try:
            cand = sorted({int(i) for i in raw_multi})
        except (TypeError, ValueError):
            return None, True, False
        return pick(
            any(i < 0 or i >= n_options for i in cand),
            lambda: (cand, False, True),
            lambda: (cand, False, False),
        )

    return pick(
        isinstance(raw_multi, list) and len(raw_multi) >= 2,
        parsed,
        lambda: (None, False, False),
    )


def _append(flaws: list[dict[str, str]], flag: bool, flaw: dict[str, str]) -> None:
    pick(flag, lambda: flaws.append(flaw), lambda: None)


def run_heuristic_checks(
    mcq: dict[str, Any],
    *,
    prior_mcqs: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Fast rule-based IWF checks before the LLM critic."""
    question = (mcq.get("question") or mcq.get("stem") or "").strip()
    options = list(
        filter(
            None,
            (str(o).strip() for o in (mcq.get("options") or mcq.get("choices") or [])),
        )
    )
    correct_index = mcq.get("correct_index")
    raw_multi = mcq.get("correct_indices")
    multi_indices, multi_invalid, multi_range = _parse_multi_indices(raw_multi, len(options))
    try:
        ci = int(correct_index)
        ci_invalid = False
    except (TypeError, ValueError):
        ci = 0
        ci_invalid = True
    hit = first_match(
        _STRUCT_RULES,
        {
            "no_q_or_opts": not question or len(options) < 2,
            "multi_invalid": multi_invalid,
            "multi_range": multi_range,
            "ci_invalid": ci_invalid,
            "ci_range": ci < 0 or ci >= len(options),
        },
    )

    def structural(msg: str) -> list[dict[str, str]]:
        return [{"code": "invalid_structure", "message": msg}]

    def rest() -> list[dict[str, str]]:
        flaws: list[dict[str, str]] = []
        lowered = [o.lower() for o in options]
        _append(
            flaws,
            len(set(lowered)) != len(lowered),
            {"code": "ambiguous_unclear", "message": "Duplicate answer options"},
        )
        none_opt = next(filter(_NONE_ALL_RE.search, options), None)
        _append(
            flaws,
            none_opt is not None,
            {"code": "none_or_all_of_above", "message": "Option uses none/all of the above"},
        )
        _append(
            flaws,
            bool(_NONE_ALL_RE.search(question)),
            {"code": "none_or_all_of_above", "message": "Stem references none/all of the above"},
        )
        _append(
            flaws,
            has_document_meta_reference(question),
            {
                "code": "meta_page_reference",
                "message": "Stem uses exam-forbidden book/page/passage framing instead of asking the concept directly",
            },
        )
        meta_opt = next(filter(has_document_meta_reference, options), None)
        _append(
            flaws,
            meta_opt is not None,
            {
                "code": "meta_page_reference",
                "message": "Option references a book, page, passage, or document",
            },
        )
        _append(
            flaws,
            bool(_NOT_SELF_CONTAINED_RE.search(question)),
            {
                "code": "not_self_contained",
                "message": "Stem dangles a reference (figure/above/example/text) only visible in the source",
            },
        )
        dang_opt = next(filter(_NOT_SELF_CONTAINED_RE.search, options), None)
        _append(
            flaws,
            dang_opt is not None,
            {
                "code": "not_self_contained",
                "message": "Option dangles a reference only visible in the source",
            },
        )
        blanks = _BLANK_RE.findall(question)
        _append(
            flaws,
            len(blanks) > 1 or bool(_ELLIPSIS_OR_BRACKET_BLANK_RE.search(question)),
            {"code": "unfocused_stem", "message": "Malformed blank (multiple ___, trailing …, or [ ])"},
        )
        _append(
            flaws,
            bool(_NEGATIVE_STEM_RE.search(question))
            and not _CAPITALIZED_NEGATIVE_RE.search(question),
            {
                "code": "negative_wording",
                "message": "Hidden lowercase negative in stem (capitalize NOT/EXCEPT or rephrase)",
            },
        )

        def longest_check() -> None:
            correct_len = len(options[ci])
            other_lens = list(
                map(
                    lambda pair: len(pair[1]),
                    filter(lambda pair: pair[0] != ci, enumerate(options)),
                )
            )

            def vs_avg() -> None:
                avg_other = sum(other_lens) / len(other_lens)
                _append(
                    flaws,
                    avg_other > 0
                    and correct_len > avg_other * 1.6
                    and correct_len - avg_other > 12,
                    {
                        "code": "longest_option_correct",
                        "message": "Correct option noticeably longer than distractors",
                    },
                )

            pick(bool(other_lens), vs_avg, lambda: None)

        pick(multi_indices is None, longest_check, lambda: None)

        def absolute_cues() -> None:
            other_abs = next(
                filter(
                    lambda pair: pair[0] != ci and _ABSOLUTE_RE.search(pair[1]),
                    enumerate(options),
                ),
                None,
            )
            _append(
                flaws,
                other_abs is not None,
                {
                    "code": "grammatical_cues",
                    "message": "Absolute terms appear on multiple options",
                },
            )

        pick(bool(_ABSOLUTE_RE.search(options[ci])), absolute_cues, lambda: None)
        _append(
            flaws,
            not question.endswith("?")
            and not _is_assertion_reason(question)
            and len(blanks) != 1,
            {"code": "unfocused_stem", "message": "Stem should be a clear question ending with ?"},
        )

        def prior_stem_check() -> None:
            match = next(
                filter(
                    lambda prior: stems_match(question, str(prior.get("question") or "")),
                    prior_mcqs or [],
                ),
                None,
            )
            _append(
                flaws,
                match is not None,
                {
                    "code": "too_similar_to_prior",
                    "message": "Stem matches a prior question on this page",
                },
            )

        pick(bool(prior_mcqs), prior_stem_check, lambda: None)
        for combo_bias in find_all_statements_combination_bias_flaws(mcq, prior_mcqs):
            flaws.append(combo_bias)
        return flaws

    return apply(
        hit.action,
        {
            "missing": lambda: structural("Question or options missing"),
            "multi_invalid": lambda: structural("correct_indices invalid"),
            "multi_range": lambda: structural("correct_indices out of range"),
            "ci_invalid": lambda: structural("correct_index invalid"),
            "ci_range": lambda: structural("correct_index out of range"),
            "ok": rest,
        },
    )


def has_fatal_heuristic_flaws(flaws: list[dict[str, str]]) -> bool:
    return any(f.get("code") in FATAL_FLAW_CODES for f in flaws)
