"""MCQ quality gate — IWF heuristics + LLM critic + rewrite loop."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.services.llm_router import complete_chat
from app.services.mcq_dedup import (
    coerce_mcq_options,
    format_prior_mcqs_block,
    has_page_reference_stem,
    is_mcq_too_similar,
    sanitize_mcq_stem,
    stems_match,
)
from app.services.prompts import get_prompt

MAX_GENERATION_ATTEMPTS = 3

FATAL_FLAW_CODES = frozenset(
    {
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
_ABSOLUTE_RE = re.compile(r"\b(always|never|only|all|none)\b", re.IGNORECASE)


class McqQualityError(Exception):
    """Raised when an MCQ cannot pass the quality gate."""


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

    if has_page_reference_stem(question):
        flaws.append(
            {
                "code": "meta_page_reference",
                "message": "Stem frames the question around the page instead of testing knowledge directly",
            }
        )

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

    if not question.endswith("?"):
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


def _complete_chat_sync(db: Session, messages: list[dict], *, log_tag: str) -> str:
    return asyncio.run(complete_chat(messages, db, log_tag=log_tag))


def _parse_mcq_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text_block = raw.strip()
    fence = re.search(r"```(?:zv-mcq|json)?\s*(\{.*?\})\s*```", text_block, re.DOTALL)
    if fence:
        text_block = fence.group(1)
    elif not text_block.startswith("{"):
        start = text_block.find("{")
        end = text_block.rfind("}")
        if start >= 0 and end > start:
            text_block = text_block[start : end + 1]
        else:
            return None
    try:
        return json.loads(text_block)
    except json.JSONDecodeError:
        return None


def _parse_critic_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text_block = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_block, re.DOTALL)
    if fence:
        text_block = fence.group(1)
    elif not text_block.startswith("{"):
        start = text_block.find("{")
        end = text_block.rfind("}")
        if start >= 0 and end > start:
            text_block = text_block[start : end + 1]
        else:
            return None
    try:
        return json.loads(text_block)
    except json.JSONDecodeError:
        return None


def _normalize_mcq_payload(data: dict[str, Any], target_aspect: dict[str, Any] | None) -> dict[str, Any]:
    question = sanitize_mcq_stem(str(data.get("question") or data.get("stem") or ""))
    options = coerce_mcq_options(data.get("options") or data.get("choices"))
    if not question or len(options) < 2:
        raise ValueError("invalid mcq")
    key = data.get("primary_concept_key") or (target_aspect or {}).get("key") or "page-concept"
    label = data.get("primary_concept") or (target_aspect or {}).get("label") or "Page concept"
    return {
        "question": question,
        "options": options,
        "correct_index": int(data.get("correct_index", 0)),
        "explanation": data.get("explanation") or "",
        "primary_concept_key": key,
        "primary_concept": label,
        "tags": data.get("tags") or ["auto"],
    }


def _critique_passes(critique: dict[str, Any]) -> bool:
    if not critique.get("pass"):
        return False
    fatal = critique.get("fatal_flaws") or []
    if any(code in FATAL_FLAW_CODES for code in fatal):
        return False
    flaw_count = int(critique.get("flaw_count") or 0)
    return flaw_count <= 1


def _merge_critique_for_rewrite(
    heuristic_flaws: list[dict[str, str]],
    critique: dict[str, Any] | None,
) -> dict[str, Any]:
    flaws = list(heuristic_flaws)
    hints: list[str] = []
    if critique:
        flaws.extend(critique.get("flaws") or [])
        fatal = critique.get("fatal_flaws") or []
        for code in fatal:
            flaws.append({"code": code, "message": code.replace("_", " ")})
        hint = (critique.get("rewrite_hints") or "").strip()
        if hint:
            hints.append(hint)
    for f in heuristic_flaws:
        hints.append(f"{f.get('code')}: {f.get('message')}")
    return {
        "flaws": flaws,
        "rewrite_hints": " ".join(hints[:6]),
        "fatal_flaws": [f.get("code") for f in flaws if f.get("code") in FATAL_FLAW_CODES],
    }


def _generate_draft_mcq(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    sequence: int,
    target_aspect: dict[str, Any] | None,
    prior_mcqs: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    excerpt = page_text[:12_000]
    aspect_line = ""
    cognitive_line = ""
    if target_aspect:
        aspect_line = (
            f"\nTarget this aspect only: {target_aspect.get('label')} "
            f"(key: {target_aspect.get('key')}).\n"
        )
        angle = (target_aspect.get("cognitive_angle") or "").strip()
        if angle:
            cognitive_line = f"Cognitive angle for this aspect: {angle}.\n"
    prior_block = format_prior_mcqs_block(prior_mcqs)

    system = get_prompt(db, "mcq_page_generate_system")
    user = (
        f"Generate exactly one multiple-choice question from this page excerpt.\n"
        f"Question index on this page: {sequence}\n"
        f"{aspect_line}{cognitive_line}\n"
        f"{prior_block}"
        f"Page text:\n{excerpt}\n\n"
        "Return only one ```zv-mcq``` JSON block. Include primary_concept_key matching the target aspect."
    )
    raw = _complete_chat_sync(
        db,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        log_tag="generate_mcq",
    )
    parsed = _parse_mcq_json(raw)
    if not parsed:
        return None
    try:
        return _normalize_mcq_payload(parsed, target_aspect)
    except ValueError:
        return None


def _rewrite_mcq(
    db: Session,
    *,
    draft: dict[str, Any],
    page_text: str,
    page_number: int,
    target_aspect: dict[str, Any] | None,
    critique_bundle: dict[str, Any],
    prior_mcqs: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    excerpt = page_text[:12_000]
    aspect_label = (target_aspect or {}).get("label") or draft.get("primary_concept") or "aspect"
    flaws_json = json.dumps(critique_bundle.get("flaws") or [], ensure_ascii=False)
    hints = critique_bundle.get("rewrite_hints") or ""
    prior_block = format_prior_mcqs_block(prior_mcqs)

    system = get_prompt(db, "mcq_rewrite_system")
    user = (
        f"Rewrite this MCQ for aspect: {aspect_label}.\n\n"
        f"Flaws to fix:\n{flaws_json}\n\n"
        f"Hints:\n{hints}\n\n"
        f"{prior_block}"
        f"Page text:\n{excerpt}\n\n"
        f"Current MCQ:\n{json.dumps(draft, ensure_ascii=False)}\n\n"
        "Return only one ```zv-mcq``` JSON block."
    )
    raw = _complete_chat_sync(
        db,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        log_tag="rewrite_mcq",
    )
    parsed = _parse_mcq_json(raw)
    if not parsed:
        return None
    try:
        return _normalize_mcq_payload(parsed, target_aspect)
    except ValueError:
        return None


def critique_mcq(
    db: Session,
    *,
    mcq: dict[str, Any],
    page_text: str,
    page_number: int,
    target_aspect: dict[str, Any] | None,
    prior_mcqs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    excerpt = page_text[:8_000]
    aspect = target_aspect or {}
    angle = (aspect.get("cognitive_angle") or "").strip()
    cognitive_angle_line = f"Cognitive angle: {angle}." if angle else ""
    prior_block = format_prior_mcqs_block(prior_mcqs) or "(none yet)"

    system = get_prompt(db, "mcq_critic_system")
    user = get_prompt(
        db,
        "mcq_critic_format",
        aspect_label=aspect.get("label") or mcq.get("primary_concept") or "aspect",
        aspect_key=aspect.get("key") or mcq.get("primary_concept_key") or "aspect",
        cognitive_angle_line=cognitive_angle_line,
        page_excerpt=excerpt,
        prior_mcqs_block=f"Prior questions on this page:\n{prior_block}",
        mcq_json=json.dumps(
            {
                "question": mcq.get("question"),
                "options": mcq.get("options"),
                "correct_index": mcq.get("correct_index"),
                "explanation": mcq.get("explanation"),
            },
            ensure_ascii=False,
        ),
    )
    raw = _complete_chat_sync(
        db,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        log_tag="critic_mcq",
    )
    parsed = _parse_critic_json(raw)
    if not parsed:
        return {
            "pass": False,
            "flaw_count": 99,
            "fatal_flaws": ["ambiguous_unclear"],
            "flaws": [{"code": "ambiguous_unclear", "message": "Critic response unparseable"}],
            "rewrite_hints": "Regenerate with a clear stem, one best answer, and plausible distractors.",
        }
    return parsed


def generate_quality_mcq(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    sequence: int,
    target_aspect: dict[str, Any] | None = None,
    prior_mcqs: list[dict[str, Any]] | None = None,
    asked_labels: list[str] | None = None,
    max_attempts: int = MAX_GENERATION_ATTEMPTS,
) -> dict[str, Any] | None:
    """Generate → heuristic check → LLM critic → embedding gate → rewrite until pass or exhausted."""
    if not page_text or not page_text.strip():
        return None

    if prior_mcqs is None and asked_labels:
        prior_mcqs = [{"aspect_label": label, "question": "", "correct_answer": ""} for label in asked_labels]

    draft: dict[str, Any] | None = None
    last_critique_bundle: dict[str, Any] = {"flaws": [], "rewrite_hints": ""}

    for attempt in range(max_attempts):
        if attempt == 0:
            draft = _generate_draft_mcq(
                db,
                page_text=page_text,
                page_number=page_number,
                sequence=sequence,
                target_aspect=target_aspect,
                prior_mcqs=prior_mcqs,
            )
        else:
            if draft is None:
                draft = _generate_draft_mcq(
                    db,
                    page_text=page_text,
                    page_number=page_number,
                    sequence=sequence,
                    target_aspect=target_aspect,
                    prior_mcqs=prior_mcqs,
                )
            elif last_critique_bundle.get("flaws") or last_critique_bundle.get("rewrite_hints"):
                draft = _rewrite_mcq(
                    db,
                    draft=draft,
                    page_text=page_text,
                    page_number=page_number,
                    target_aspect=target_aspect,
                    critique_bundle=last_critique_bundle,
                    prior_mcqs=prior_mcqs,
                )
            else:
                draft = _generate_draft_mcq(
                    db,
                    page_text=page_text,
                    page_number=page_number,
                    sequence=sequence,
                    target_aspect=target_aspect,
                    prior_mcqs=prior_mcqs,
                )

        if draft is None:
            last_critique_bundle = {
                "flaws": [{"code": "invalid_structure", "message": "Generation failed"}],
                "rewrite_hints": "Return valid JSON with question, options, correct_index.",
            }
            continue

        heuristic_flaws = run_heuristic_checks(draft, prior_mcqs=prior_mcqs)
        if has_fatal_heuristic_flaws(heuristic_flaws):
            last_critique_bundle = _merge_critique_for_rewrite(heuristic_flaws, None)
            continue

        critique = critique_mcq(
            db,
            mcq=draft,
            page_text=page_text,
            page_number=page_number,
            target_aspect=target_aspect,
            prior_mcqs=prior_mcqs,
        )
        extra_flaws = [f for f in (critique.get("flaws") or []) if f.get("code") not in FATAL_FLAW_CODES]
        combined_count = len(heuristic_flaws) + len(extra_flaws) + len(critique.get("fatal_flaws") or [])
        critique = {**critique, "flaw_count": max(int(critique.get("flaw_count") or 0), combined_count)}

        if _critique_passes(critique):
            too_similar, max_sim = is_mcq_too_similar(draft, prior_mcqs)
            if too_similar:
                last_critique_bundle = {
                    "flaws": [
                        {
                            "code": "too_similar_to_prior",
                            "message": f"Embedding similarity {max_sim:.2f} to a prior question",
                        }
                    ],
                    "rewrite_hints": "Test a different fact and use a clearly different stem from all prior questions.",
                    "fatal_flaws": ["too_similar_to_prior"],
                }
                continue

            draft["quality"] = {
                "pass": True,
                "flaw_count": critique.get("flaw_count", 0),
                "cognitive_level": critique.get("cognitive_level"),
                "matches_aspect": critique.get("matches_aspect"),
                "provokes_understanding": critique.get("provokes_understanding"),
                "attempts": attempt + 1,
                "heuristic_flaws": heuristic_flaws,
                "max_similarity_to_prior": max_sim,
            }
            if target_aspect and target_aspect.get("cognitive_angle"):
                draft["cognitive_angle"] = target_aspect["cognitive_angle"]
            return draft

        last_critique_bundle = _merge_critique_for_rewrite(heuristic_flaws, critique)

    return None
