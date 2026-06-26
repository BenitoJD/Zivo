"""MCQ quality gate — IWF heuristics + LLM critic + rewrite loop."""

from __future__ import annotations

import json
import os
import random
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.mcq_dedup import (
    coerce_mcq_options,
    embed_signature_cached,
    format_prior_mcqs_block,
    has_document_meta_reference,
    is_mcq_too_similar,
    mcq_signature,
    prior_mcq_embeddings,
    sanitize_mcq_explanation,
    sanitize_mcq_stem,
    stems_match,
    SUBJECT_MATTER_PREFIX,
)
from app.services.prompts import get_prompt
from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens

_PAGE_EXCERPT_MAX_TOKENS = PAGE_INPUT_MAX_TOKENS

MAX_GENERATION_ATTEMPTS = 3

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


def _complete_chat_sync(
    db: Session, messages: list[dict], *, log_tag: str, model_id: uuid.UUID | None = None
) -> str:
    return run_coro_in_worker(
        acomplete_chat(messages, db, log_tag=log_tag, model_id=model_id)
    )


def _content_style_line(content_type: str | None) -> str:
    """One-line generation directive for the material's content_type.

    Returns '' unless content-aware generation is enabled, so the universal MCQ
    rubric is unchanged by default. When on, it shifts the *kind* of thinking and
    the truth model (e.g. interpretation for narrative) without touching the gate.
    """
    from app.config import get_settings

    if not get_settings().content_aware_generation:
        return ""
    from app.services.prompts import content_type_style

    style = content_type_style(content_type)
    return f"\nMATERIAL TYPE — {style}\n" if style else ""


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
    return _parse_mcq_json(raw)


# Fenced-block splitter: captures the JSON inside each ```zv-mcq ... ``` block.
# Non-greedy on the inner JSON (each MCQ block is flat, so this is safe) and
# finds ALL blocks rather than just the first. Used by one-call batch generation.
_FENCED_MCQ_RE = re.compile(r"```(?:zv-mcq|json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_mcq_blocks(raw: str) -> list[dict[str, Any]]:
    """Extract every MCQ JSON object from a multi-block LLM response.

    Handles fenced ```zv-mcq blocks (the normal case) and falls back to scanning
    for top-level JSON objects if the model omitted fences. Each candidate is
    json.loads'd; unparseable candidates are skipped individually so one bad
    block never fails the whole batch.
    """
    if not raw or not raw.strip():
        return []
    text_block = raw.strip()
    out: list[dict[str, Any]] = []

    # Primary path: fenced blocks.
    for match in _FENCED_MCQ_RE.finditer(text_block):
        try:
            obj = json.loads(match.group(1))
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue

    if out:
        return out

    # Fallback: the model returned a bare JSON array of MCQ objects, or a run of
    # brace-delimited objects without fences. Try a JSON array first, then a
    # whole-text parse, then a brace scan.
    if text_block.startswith("["):
        try:
            arr = json.loads(text_block)
            if isinstance(arr, list):
                return [o for o in arr if isinstance(o, dict)]
        except json.JSONDecodeError:
            pass

    if text_block.startswith("{"):
        try:
            obj = json.loads(text_block)
            if isinstance(obj, dict):
                return [obj]
        except json.JSONDecodeError:
            pass

    # Last resort: pull each top-level {...} span. Greedy-with-balancing isn't
    # trivial with regex, so we find each '{'...'{' pair at the shallowest level
    # via a simple depth scan — robust enough for flat MCQ objects.
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text_block):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text_block[start : i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return out


def _trim_explanation(text: str, *, max_chars: int = 160) -> str:
    """Cap an MCQ explanation to one concise sentence (output-token economy).

    The model is told to keep explanations to one short sentence, but it often
    over-writes. Trimming here guarantees a compact stored payload and — more
    importantly — fewer emitted tokens during generation means faster completion.
    Keeps the first sentence; ellipsizes only if a second sentence was present.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    # Take up to the first sentence boundary.
    for sep in (". ", "! ", "? "):
        idx = cleaned.find(sep)
        if 0 < idx < max_chars:
            return cleaned[: idx + 1].strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _normalize_mcq_payload(data: dict[str, Any], target_aspect: dict[str, Any] | None) -> dict[str, Any]:
    question = sanitize_mcq_stem(str(data.get("question") or data.get("stem") or ""))
    options = coerce_mcq_options(data.get("options") or data.get("choices"))
    explanation = sanitize_mcq_explanation(
        _trim_explanation(str(data.get("explanation") or ""))
    )
    if not question or len(options) < 2:
        raise ValueError("invalid mcq")
    key = data.get("primary_concept_key") or (target_aspect or {}).get("key") or "page-concept"
    label = data.get("primary_concept") or (target_aspect or {}).get("label") or "Page concept"
    return {
        "question": question,
        "options": options,
        "correct_index": int(data.get("correct_index", 0)),
        "explanation": explanation,
        "primary_concept_key": key,
        "primary_concept": label,
        "tags": data.get("tags") or ["auto"],
        # Wikidata concept tags — the LLM emits these from the source material;
        # persist-time code in generation_graph.py links them to intel.entity rows.
        "tested_concepts": _coerce_tested_concepts(data.get("tested_concepts")),
    }


# Shape: [{"qid": "Q80001", "label": "Photosynthesis"}]
_QID_RE = re.compile(r"^Q\d+$", re.IGNORECASE)


def _coerce_tested_concepts(raw: Any) -> list[dict[str, str]]:
    """Normalize the model's tested_concepts output to [{qid, label}]."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        if not _QID_RE.match(qid):
            continue
        qid = qid.upper()
        if qid in seen:
            continue
        seen.add(qid)
        label = str(item.get("label") or "").strip()[:200]
        if not label:
            continue
        out.append({"qid": qid, "label": label})
    return out


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
    model_id: uuid.UUID | None = None,
    content_type: str | None = None,
) -> dict[str, Any] | None:
    excerpt = truncate_to_tokens(page_text, _PAGE_EXCERPT_MAX_TOKENS)
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
    # Stable-prefix message ordering for provider prompt caching: the system
    # prompt and page text are byte-identical across every question on a page
    # (and across refill batches), so they form a cacheable prefix. Only the
    # per-question variable part (aspect, prior questions, index) sits in the
    # trailing message. Z.AI / OpenAI-compatible providers cache the leading
    # prefix automatically and skip re-processing the ~12k-char page text.
    instructions = (
        f"Generate exactly one multiple-choice question from the subject matter below.\n"
        f"Question index on this page: {sequence}\n"
        f"{aspect_line}{cognitive_line}{_content_style_line(content_type)}\n"
        f"{prior_block}"
        "Return only one ```zv-mcq``` JSON block. Include primary_concept_key matching the target aspect."
    )
    raw = _complete_chat_sync(
        db,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\n{excerpt}"},
            {"role": "user", "content": instructions},
        ],
        log_tag="generate_mcq",
        model_id=model_id,
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
    model_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    excerpt = truncate_to_tokens(page_text, _PAGE_EXCERPT_MAX_TOKENS)
    aspect_label = (target_aspect or {}).get("label") or draft.get("primary_concept") or "aspect"
    flaws_json = json.dumps(critique_bundle.get("flaws") or [], ensure_ascii=False)
    hints = critique_bundle.get("rewrite_hints") or ""
    prior_block = format_prior_mcqs_block(prior_mcqs)

    system = get_prompt(db, "mcq_rewrite_system")
    # Stable-prefix ordering for caching (page text + system prompt first).
    instructions = (
        f"Rewrite this MCQ for aspect: {aspect_label}.\n\n"
        f"Flaws to fix:\n{flaws_json}\n\n"
        f"Hints:\n{hints}\n\n"
        f"{prior_block}"
        f"Current MCQ:\n{json.dumps(draft, ensure_ascii=False)}\n\n"
        "Return only one ```zv-mcq``` JSON block."
    )
    raw = _complete_chat_sync(
        db,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\n{excerpt}"},
            {"role": "user", "content": instructions},
        ],
        log_tag="rewrite_mcq",
        model_id=model_id,
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
    model_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    excerpt = truncate_to_tokens(page_text, _PAGE_EXCERPT_MAX_TOKENS)
    aspect = target_aspect or {}
    angle = (aspect.get("cognitive_angle") or "").strip()
    cognitive_angle_line = f"Cognitive angle: {angle}." if angle else ""
    prior_block = format_prior_mcqs_block(prior_mcqs) or "(none yet)"

    system = get_prompt(db, "mcq_critic_system")
    critique_body = get_prompt(
        db,
        "mcq_critic_format",
        aspect_label=aspect.get("label") or mcq.get("primary_concept") or "aspect",
        aspect_key=aspect.get("key") or mcq.get("primary_concept_key") or "aspect",
        cognitive_angle_line=cognitive_angle_line,
        page_excerpt="",
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
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\n{excerpt}"},
            {"role": "user", "content": critique_body},
        ],
        log_tag="critic_mcq",
        model_id=model_id,
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
    model_id: uuid.UUID | None = None,
    content_type: str | None = None,
) -> dict[str, Any] | None:
    """Generate → heuristic check → LLM critic → embedding gate → rewrite until pass or exhausted."""
    if not page_text or not page_text.strip():
        return None

    if prior_mcqs is None and asked_labels:
        prior_mcqs = [{"aspect_label": label, "question": "", "correct_answer": ""} for label in asked_labels]

    # Pin every generation call to one model instead of round-robining the pool.
    # A parallel batch split across models of different speed waits on the slowest;
    # pinning to the default collapses batch wall-clock. Interactive chat keeps
    # using the pool for failover. Falls back to pool behavior if no default set.
    if model_id is None:
        from app.services.llm_registry import default_chat_model_id

        model_id = default_chat_model_id(db)

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
                model_id=model_id,
                content_type=content_type,
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
                    model_id=model_id,
                    content_type=content_type,
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
                    model_id=model_id,
                )
            else:
                draft = _generate_draft_mcq(
                    db,
                    page_text=page_text,
                    page_number=page_number,
                    sequence=sequence,
                    target_aspect=target_aspect,
                    prior_mcqs=prior_mcqs,
                    model_id=model_id,
                    content_type=content_type,
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

        if not heuristic_flaws:
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
                "flaw_count": 0,
                "attempts": attempt + 1,
                "fast_path": True,
                "heuristic_flaws": [],
                "max_similarity_to_prior": max_sim,
            }
            if target_aspect and target_aspect.get("cognitive_angle"):
                draft["cognitive_angle"] = target_aspect["cognitive_angle"]
            return draft

        critique = critique_mcq(
            db,
            mcq=draft,
            page_text=page_text,
            page_number=page_number,
            target_aspect=target_aspect,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
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
                "attempts": attempt + 1,
                "heuristic_flaws": heuristic_flaws,
                "max_similarity_to_prior": max_sim,
            }
            if target_aspect and target_aspect.get("cognitive_angle"):
                draft["cognitive_angle"] = target_aspect["cognitive_angle"]
            return draft

        last_critique_bundle = _merge_critique_for_rewrite(heuristic_flaws, critique)

    return None


# Cap on how many MCQs to ask for in a single LLM call. Past ~20-30 structured
# objects, LLMs reliably degrade (repetition, dropped JSON alignment, filler
# distractors). 5 is safely within the reliable range. For budgets > 5, call
# this multiple times — prompt caching makes the repeat calls cheap.
BATCH_MCQ_CAP = 5
CRITIC_SAMPLE_RATE = float(os.getenv("ZIVO_CRITIC_SAMPLE_RATE", "0.25"))


def _generate_batch_drafts(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    targets: list[dict[str, Any]],
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None = None,
    aspect_hints: str = "",
    content_type: str | None = None,
) -> list[dict[str, Any]]:
    """One LLM call producing up to N MCQs (one per target aspect).

    Returns normalized payload dicts for every block the model emitted. The
    quality gates (heuristics, embedding similarity) run on each in the caller.
    """
    excerpt = truncate_to_tokens(page_text, _PAGE_EXCERPT_MAX_TOKENS)
    targets_block = "\n".join(
        f"{i + 1}. {t.get('label')} (key: {t.get('key')})"
        + (f" — angle: {t.get('cognitive_angle')}" if t.get("cognitive_angle") else "")
        for i, t in enumerate(targets)
    )
    prior_block = format_prior_mcqs_block(prior_mcqs)
    hints_block = f"\n\nAspect focus hints (variable — not part of cached prefix):\n{aspect_hints}" if aspect_hints else ""

    system = get_prompt(db, "mcq_page_generate_system")
    instructions = (
        f"Generate exactly {len(targets)} multiple-choice questions — ONE per target aspect below. "
        f"Each must test a distinct idea from the subject matter.\n"
        f"{_content_style_line(content_type)}\n"
        f"Target aspects:\n{targets_block}\n\n"
        f"{prior_block}"
        f"{hints_block}\n"
        f"Return ONLY {len(targets)} ```zv-mcq``` JSON blocks, one per aspect, in order. "
        "Each block's primary_concept_key must match its target aspect key. "
        "Be economical with tokens: no commentary, explanation at most ONE short sentence."
    )
    raw = _complete_chat_sync(
        db,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\n{excerpt}"},
            {"role": "user", "content": instructions},
        ],
        log_tag="generate_mcq_batch",
        model_id=model_id,
    )
    blocks = _parse_mcq_blocks(raw)
    # Pair each parsed block with its target aspect (in order). If the model
    # returned fewer blocks than targets, we keep what we got. Normalization is
    # per-block so one malformed block can't fail the rest.
    normalized: list[dict[str, Any]] = []
    for idx, data in enumerate(blocks):
        target = targets[idx] if idx < len(targets) else None
        try:
            normalized.append(_normalize_mcq_payload(data, target))
        except ValueError:
            continue
    return normalized


def generate_quality_mcq_batch(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    targets: list[dict[str, Any]],
    prior_mcqs: list[dict[str, Any]] | None = None,
    model_id: uuid.UUID | None = None,
    aspect_hints: str = "",
    content_type: str | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Generate N MCQs in ONE LLM call, then run quality gates on each.

    Returns (target_aspect, payload) pairs for MCQs that pass heuristics,
    embedding similarity, and sampled LLM critic checks.
    """
    if not page_text or not page_text.strip() or not targets:
        return []

    targets = targets[:BATCH_MCQ_CAP]
    if model_id is None:
        from app.services.llm_registry import default_chat_model_id

        model_id = default_chat_model_id(db)

    # Generation response cache: skip the LLM call if we've already generated
    # drafts for this (page context, aspect set). The quality gate below still
    # runs on every retrieval — only the generation call is skipped. DB-backed
    # so the 4 CPU workers share one cache and a restart doesn't re-pay the LLM.
    from app.services.generation_cache import batch_drafts_key, get as cache_get, put as cache_put

    drafts_cache_key = batch_drafts_key(page_number, targets, prior_mcqs)
    drafts = cache_get(db, kind="batch_drafts", cache_key=drafts_cache_key)
    if drafts is None:
        drafts = _generate_batch_drafts(
            db,
            page_text=page_text,
            page_number=page_number,
            targets=targets,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
            aspect_hints=aspect_hints,
            content_type=content_type,
        )
        if drafts:
            cache_put(db, kind="batch_drafts", cache_key=drafts_cache_key, value=drafts)
    if not drafts:
        return []

    # Run the fast-path quality gate per draft. Intra-batch near-dupes are
    # caught by tracking accepted drafts as the "prior" for the next check.
    accepted: list[tuple[dict[str, Any], dict[str, Any]]] = []
    accepted_payloads: list[dict[str, Any]] = []
    prior_embeddings = prior_mcq_embeddings(list(prior_mcqs or []))
    for idx, draft in enumerate(drafts):
        target = targets[idx] if idx < len(targets) else None
        check_against = list(prior_mcqs or []) + accepted_payloads

        heuristic_flaws = run_heuristic_checks(draft, prior_mcqs=check_against)
        if has_fatal_heuristic_flaws(heuristic_flaws):
            continue

        too_similar, max_sim = is_mcq_too_similar(
            draft,
            check_against,
            prior_embeddings=prior_embeddings,
        )
        if too_similar:
            continue

        run_critic = bool(heuristic_flaws) or idx == 0 or random.random() < CRITIC_SAMPLE_RATE
        critic_meta: dict[str, Any] | None = None
        if run_critic:
            critique = critique_mcq(
                db,
                mcq=draft,
                page_text=page_text,
                page_number=page_number,
                target_aspect=target,
                prior_mcqs=check_against,
                model_id=model_id,
            )
            critic_meta = critique
            if not _critique_passes(critique):
                rewritten = _rewrite_mcq(
                    db,
                    draft=draft,
                    page_text=page_text,
                    page_number=page_number,
                    target_aspect=target,
                    critique_bundle=_merge_critique_for_rewrite(heuristic_flaws, critique),
                    prior_mcqs=check_against,
                    model_id=model_id,
                )
                if not rewritten:
                    continue
                draft = rewritten
                heuristic_flaws = run_heuristic_checks(draft, prior_mcqs=check_against)
                if has_fatal_heuristic_flaws(heuristic_flaws):
                    continue
                too_similar, max_sim = is_mcq_too_similar(
                    draft,
                    check_against,
                    prior_embeddings=prior_embeddings,
                )
                if too_similar:
                    continue
                critique = critique_mcq(
                    db,
                    mcq=draft,
                    page_text=page_text,
                    page_number=page_number,
                    target_aspect=target,
                    prior_mcqs=check_against,
                    model_id=model_id,
                )
                critic_meta = critique
                if not _critique_passes(critique):
                    continue

        draft["quality"] = {
            "pass": True,
            "flaw_count": len(heuristic_flaws),
            "attempts": 2 if run_critic and critic_meta else 1,
            "fast_path": not run_critic,
            "batch": True,
            "critic_sampled": run_critic,
            "heuristic_flaws": heuristic_flaws,
            "max_similarity_to_prior": max_sim,
        }
        if critic_meta:
            draft["quality"]["critic_sampled"] = True
        if target and target.get("cognitive_angle"):
            draft["cognitive_angle"] = target["cognitive_angle"]
        accepted.append((target, draft))
        accepted_payloads.append(draft)
        prior_embeddings.append(embed_signature_cached(mcq_signature(draft)))

    return accepted
