"""MCQ quality gate — IWF heuristics + LLM critic + rewrite loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections import OrderedDict
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


def _complete_chat_sync(
    db: Session, messages: list[dict], *, log_tag: str, model_id: uuid.UUID | None = None
) -> str:
    return asyncio.run(complete_chat(messages, db, log_tag=log_tag, model_id=model_id))


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
    if not question or len(options) < 2:
        raise ValueError("invalid mcq")
    key = data.get("primary_concept_key") or (target_aspect or {}).get("key") or "page-concept"
    label = data.get("primary_concept") or (target_aspect or {}).get("label") or "Page concept"
    return {
        "question": question,
        "options": options,
        "correct_index": int(data.get("correct_index", 0)),
        "explanation": _trim_explanation(str(data.get("explanation") or "")),
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
    model_id: uuid.UUID | None = None,
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
    # Stable-prefix message ordering for provider prompt caching: the system
    # prompt and page text are byte-identical across every question on a page
    # (and across refill batches), so they form a cacheable prefix. Only the
    # per-question variable part (aspect, prior questions, index) sits in the
    # trailing message. Z.AI / OpenAI-compatible providers cache the leading
    # prefix automatically and skip re-processing the ~12k-char page text.
    instructions = (
        f"Generate exactly one multiple-choice question from this page excerpt.\n"
        f"Question index on this page: {sequence}\n"
        f"{aspect_line}{cognitive_line}\n"
        f"{prior_block}"
        "Return only one ```zv-mcq``` JSON block. Include primary_concept_key matching the target aspect."
    )
    raw = _complete_chat_sync(
        db,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Page text:\n{excerpt}"},
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
    excerpt = page_text[:12_000]
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
            {"role": "user", "content": f"Page text:\n{excerpt}"},
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


# Cap on how many MCQs to ask for in a single LLM call. Past ~20-30 structured
# objects, LLMs reliably degrade (repetition, dropped JSON alignment, filler
# distractors). 5 is safely within the reliable range. For budgets > 5, call
# this multiple times — prompt caching makes the repeat calls cheap.
BATCH_MCQ_CAP = 5


class _BatchDraftCache:
    """In-process LRU cache of batch draft responses (no DB migration needed).

    Caches the *drafts* (pre-quality-gate) keyed by a deterministic hash of the
    page context + sorted aspect keys. The quality gate still runs on every
    retrieval (we never bypass heuristics/embedding checks) — only the LLM
    generation call is skipped on a hit. Highest value for rapid re-queries of
    the same page (demo doc, a page re-generated within the TTL) and avoids
    paying for a DB-backed cache table + migration for a lower-volume win.
    """

    def __init__(self, max_entries: int = 64) -> None:
        self._store: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._max = max_entries

    def _key(self, page_text: str, targets: list[dict[str, Any]]) -> str:
        aspect_keys = sorted(str(t.get("key") or "") for t in targets)
        h = hashlib.sha256()
        h.update(page_text.encode("utf-8", "ignore"))
        h.update(b"\x1f")
        h.update("\x1e".join(aspect_keys).encode("utf-8", "ignore"))
        return h.hexdigest()

    def get(self, page_text: str, targets: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        key = self._key(page_text, targets)
        hits = self._store.get(key)
        if hits is None:
            return None
        # Refresh recency.
        self._store.move_to_end(key)
        # Return deep copies so callers can't mutate the cache.
        return [dict(d) for d in hits]

    def put(self, page_text: str, targets: list[dict[str, Any]], drafts: list[dict[str, Any]]) -> None:
        if not drafts:
            return
        key = self._key(page_text, targets)
        self._store[key] = [dict(d) for d in drafts]
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)


_batch_draft_cache = _BatchDraftCache()


def _generate_batch_drafts(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    targets: list[dict[str, Any]],
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One LLM call producing up to N MCQs (one per target aspect).

    Returns normalized payload dicts for every block the model emitted. The
    quality gates (heuristics, embedding similarity) run on each in the caller.
    """
    excerpt = page_text[:12_000]
    targets_block = "\n".join(
        f"{i + 1}. {t.get('label')} (key: {t.get('key')})"
        + (f" — angle: {t.get('cognitive_angle')}" if t.get("cognitive_angle") else "")
        for i, t in enumerate(targets)
    )
    prior_block = format_prior_mcqs_block(prior_mcqs)

    system = get_prompt(db, "mcq_page_generate_system")
    instructions = (
        f"Generate exactly {len(targets)} multiple-choice questions — ONE per target aspect below. "
        f"Each must test a distinct idea from the page.\n\n"
        f"Target aspects:\n{targets_block}\n\n"
        f"{prior_block}"
        f"Return ONLY {len(targets)} ```zv-mcq``` JSON blocks, one per aspect, in order. "
        "Each block's primary_concept_key must match its target aspect key. "
        "Be economical with tokens: no commentary, explanation at most ONE short sentence."
    )
    raw = _complete_chat_sync(
        db,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Page text:\n{excerpt}"},
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
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Generate N MCQs in ONE LLM call, then run the quality gate on each.

    Returns (target_aspect, payload) pairs for the MCQs that pass the fast-path
    quality gate (heuristic checks + embedding-similarity vs priors). MCQs that
    fail a gate are dropped individually — a bad one never poisons the batch.

    Unlike generate_quality_mcq's per-question critic/rewrite loop, batch mode
    uses the fast path only: heuristics + embedding similarity. The critic is
    deferred (same principle as the single-MCQ fast path) because the heuristics
    catch the structural flaws and embedding similarity catches near-dupes. This
    keeps the batch to a single LLM call for latency; any question that needs a
    critic or rewrite will be caught later by the quality gate on refill.
    """
    if not page_text or not page_text.strip() or not targets:
        return []

    targets = targets[:BATCH_MCQ_CAP]
    if model_id is None:
        from app.services.llm_registry import default_chat_model_id

        model_id = default_chat_model_id(db)

    # Generation response cache: skip the LLM call if we've already generated
    # drafts for this (page context, aspect set). The quality gate below still
    # runs on every retrieval — only the generation call is skipped. Highest
    # value for rapid re-queries (demo doc, same page within the process TTL).
    drafts = _batch_draft_cache.get(page_text, targets)
    if drafts is None:
        drafts = _generate_batch_drafts(
            db,
            page_text=page_text,
            page_number=page_number,
            targets=targets,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
        )
        if drafts:
            _batch_draft_cache.put(page_text, targets, drafts)
    if not drafts:
        return []

    # Run the fast-path quality gate per draft. Intra-batch near-dupes are
    # caught by tracking accepted drafts as the "prior" for the next check.
    accepted: list[tuple[dict[str, Any], dict[str, Any]]] = []
    accepted_payloads: list[dict[str, Any]] = []
    for idx, draft in enumerate(drafts):
        target = targets[idx] if idx < len(targets) else None
        check_against = list(prior_mcqs or []) + accepted_payloads

        heuristic_flaws = run_heuristic_checks(draft, prior_mcqs=prior_mcqs)
        if has_fatal_heuristic_flaws(heuristic_flaws):
            continue

        too_similar, max_sim = is_mcq_too_similar(draft, check_against)
        if too_similar:
            continue

        draft["quality"] = {
            "pass": True,
            "flaw_count": len(heuristic_flaws),
            "attempts": 1,
            "fast_path": True,
            "batch": True,
            "heuristic_flaws": heuristic_flaws,
            "max_similarity_to_prior": max_sim,
        }
        if target and target.get("cognitive_angle"):
            draft["cognitive_angle"] = target["cognitive_angle"]
        accepted.append((target, draft))
        accepted_payloads.append(draft)

    return accepted
