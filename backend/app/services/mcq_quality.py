"""MCQ quality gate — IWF heuristics + LLM critic + rewrite loop."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import uuid
from collections import Counter
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.mcq_dedup import (
    embed_signature_cached,
    format_prior_mcqs_block,
    is_mcq_too_similar,
    mcq_signature,
    prior_mcq_embeddings,
    SUBJECT_MATTER_PREFIX,
)
from app.services.prompts import get_prompt
from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens
from app.services.mcq_heuristics import (
    FATAL_FLAW_CODES,
    has_fatal_heuristic_flaws,
    run_heuristic_checks,
)
from app.services.mcq_parsing import (
    _parse_mcq_json,
    _parse_critic_json,
    _parse_mcq_blocks,
    _normalize_mcq_payload,
)

logger = logging.getLogger(__name__)

_PAGE_EXCERPT_MAX_TOKENS = PAGE_INPUT_MAX_TOKENS

MAX_GENERATION_ATTEMPTS = 3

def _complete_chat_sync(
    db: Session, messages: list[dict], *, log_tag: str, model_id: uuid.UUID | None = None
) -> str:
    try:
        return run_coro_in_worker(
            acomplete_chat(messages, db, log_tag=log_tag, model_id=model_id)
        )
    except Exception:
        # Generation pins one model for batch speed; if that model stalls or errors,
        # fall back to the pool (failover) so a single wedged provider can't block
        # question generation entirely.
        if model_id is None:
            raise
        return run_coro_in_worker(
            acomplete_chat(messages, db, log_tag=log_tag, model_id=None)
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
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    options = [str(o).strip() for o in (mcq.get("options") or []) if str(o).strip()]
    stem = str(mcq.get("question") or mcq.get("stem") or "")
    critic_key = content_hash_key(
        "critic",
        stem,
        "|".join(options),
        mcq.get("correct_index"),
        hashlib.sha256((page_text or "").encode("utf-8", "ignore")).hexdigest()[:32],
        model_id,
    )
    hit = cache_get(db, kind="critic_verdict", cache_key=critic_key)
    if isinstance(hit, dict) and hit:
        return hit

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
    cache_put(db, kind="critic_verdict", cache_key=critic_key, value=parsed)
    return parsed


def verify_answer_key(
    db: Session,
    *,
    mcq: dict[str, Any],
    page_text: str,
    model_id: uuid.UUID | None = None,
) -> dict[str, str] | None:
    """Independently re-solve the question from the source — BLIND to the marked key.

    The single most important correctness guarantee: a confidently wrong answer
    key is the worst failure an assessment system can make. A blind solver gets
    the source + question + options (not which is marked correct), picks the
    best-supported option itself, and we compare. Returns a fatal flaw dict when
    the item is untrustworthy, else None.

    Conservative by design: an unparseable/failed verification returns None (we
    do not reject a good item just because the verifier hiccuped — the critic and
    heuristics remain). It only rejects on a *confident* disagreement.
    """
    from app.services.chunk_map_cache import verify_verdict_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    options = [str(o).strip() for o in (mcq.get("options") or []) if str(o).strip()]
    # Multi-select ("select all that apply") items are verified against the full
    # correct SET, not a single index — the single-answer verifier would wrongly
    # flag them as "more than one correct". Route them to the set-based path.
    marked_indices = mcq.get("correct_indices")
    if isinstance(marked_indices, list) and len(marked_indices) >= 2:
        return _verify_answer_key_multi(
            db, mcq=mcq, options=options, page_text=page_text, model_id=model_id
        )
    try:
        marked = int(mcq.get("correct_index"))
    except (TypeError, ValueError):
        return None
    if len(options) < 2 or marked < 0 or marked >= len(options):
        return None  # structural problems are the heuristic gate's job

    cache_key = verify_verdict_key(
        stem=str(mcq.get("question") or mcq.get("stem") or ""),
        options=options,
        correct_index=marked,
        page_text=page_text,
        model_id=model_id,
    )
    hit = cache_get(db, kind="verify_verdict", cache_key=cache_key)
    if isinstance(hit, dict):
        if hit.get("ok") is True:
            return None
        flaw = hit.get("flaw")
        return flaw if isinstance(flaw, dict) else None

    result, decisive = _verify_answer_key_uncached(
        db, mcq=mcq, options=options, marked=marked, page_text=page_text, model_id=model_id
    )
    if decisive:
        cache_put(
            db,
            kind="verify_verdict",
            cache_key=cache_key,
            value={"ok": True} if result is None else {"ok": False, "flaw": result},
        )
    return result


def _verify_answer_key_uncached(
    db: Session,
    *,
    mcq: dict[str, Any],
    options: list[str],
    marked: int,
    page_text: str,
    model_id: uuid.UUID | None = None,
) -> tuple[dict[str, str] | None, bool]:
    """Return (flaw_or_None, decisive). decisive=False → do not cache (parse fail)."""
    excerpt = truncate_to_tokens(page_text, _PAGE_EXCERPT_MAX_TOKENS)
    options_block = "\n".join(f"{i}. {opt}" for i, opt in enumerate(options))
    system = get_prompt(db, "mcq_verify_system")
    body = get_prompt(
        db,
        "mcq_verify_format",
        question=mcq.get("question") or mcq.get("stem") or "",
        options_block=options_block,
    )
    raw = _complete_chat_sync(
        db,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\n{excerpt}"},
            {"role": "user", "content": body},
        ],
        log_tag="verify_mcq",
        model_id=model_id,
    )
    parsed = _parse_critic_json(raw)
    if not parsed:
        return None, False  # verifier failed — don't punish / don't cache

    if parsed.get("none_defensible") is True:
        return {"code": "not_grounded", "message": "Verifier: no option is supported by the source"}, True
    if parsed.get("multiple_defensible") is True:
        return {
            "code": "more_than_one_correct",
            "message": "Verifier: two or more options are defensible",
        }, True
    try:
        independent = int(parsed.get("answer_index"))
    except (TypeError, ValueError):
        return None, False
    # An out-of-range index is a malformed verifier reply, not a real
    # disagreement — don't reject a good item over it (stay conservative).
    if not (0 <= independent < len(options)):
        return None, False
    if independent != marked:
        return {
            "code": "wrong_answer_key",
            "message": (
                f"Verifier independently chose option {independent}, not the marked {marked}"
            ),
        }, True
    return None, True


def _verify_answer_key_multi(
    db: Session,
    *,
    mcq: dict[str, Any],
    options: list[str],
    page_text: str,
    model_id: uuid.UUID | None = None,
) -> dict[str, str] | None:
    """Blind set-verifier for select-all-that-apply items.

    Asks the solver which options the source supports (the full set), then compares
    to the marked set. Conservative: an unparseable/failed reply returns None.
    """
    marked = sorted(
        {i for i in (mcq.get("correct_indices") or []) if isinstance(i, int) and 0 <= i < len(options)}
    )
    if len(options) < 2 or len(marked) < 2:
        return None

    excerpt = truncate_to_tokens(page_text, _PAGE_EXCERPT_MAX_TOKENS)
    options_block = "\n".join(f"{i}. {opt}" for i, opt in enumerate(options))
    system = get_prompt(db, "mcq_verify_multi_system")
    body = get_prompt(
        db,
        "mcq_verify_multi_format",
        question=mcq.get("question") or mcq.get("stem") or "",
        options_block=options_block,
    )
    raw = _complete_chat_sync(
        db,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\n{excerpt}"},
            {"role": "user", "content": body},
        ],
        log_tag="verify_mcq_multi",
        model_id=model_id,
    )
    parsed = _parse_critic_json(raw)
    if not parsed:
        return None  # verifier failed — don't punish the item

    if parsed.get("none_defensible") is True:
        return {"code": "not_grounded", "message": "Verifier: no option is supported by the source"}
    raw_indices = parsed.get("answer_indices")
    if not isinstance(raw_indices, list):
        return None
    independent = sorted(
        {int(i) for i in raw_indices if isinstance(i, (int, float)) and 0 <= int(i) < len(options)}
    )
    if not independent:
        return None  # malformed reply, not a real disagreement — stay conservative
    if independent != marked:
        return {
            "code": "wrong_answer_key",
            "message": (
                f"Verifier independently chose options {independent}, not the marked {marked}"
            ),
        }
    return None


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

    # Drafts can use a faster/cheaper model; the rewrite + critic + verifier keep
    # the strong default (they fix and judge, where quality matters most).
    from app.services.llm_registry import draft_chat_model_id

    draft_model_id = draft_chat_model_id(db) or model_id

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
                model_id=draft_model_id,
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
                    model_id=draft_model_id,
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
                    model_id=draft_model_id,
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

        from app.config import get_settings

        if get_settings().verify_answer_key:
            key_flaw = verify_answer_key(db, mcq=draft, page_text=page_text, model_id=model_id)
            if key_flaw is not None:
                last_critique_bundle = _merge_critique_for_rewrite([key_flaw], None)
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
# The critic is the cheapest, highest-leverage quality gate and prompt caching makes
# the repeated page context nearly free — so critique EVERY item by default, not a
# 25% sample. (Evaluation is the moat; don't skip it to save a few tokens.) Still an
# env knob for cost tuning.
CRITIC_SAMPLE_RATE = float(os.getenv("ZIVO_CRITIC_SAMPLE_RATE", "1.0"))


def _draft_score(draft: dict[str, Any]) -> tuple[int, int, float]:
    """Lower is better. Free (no-LLM) structural-quality signal for best-of-N
    selection: fewest fatal flaws, then fewest total flaws, then the smallest gap
    between the correct option's length and the others' mean (guards the
    longest-answer give-away)."""
    flaws = run_heuristic_checks(draft)
    fatal = sum(1 for f in flaws if f.get("code") in FATAL_FLAW_CODES)
    options = [str(o) for o in (draft.get("options") or [])]
    gap = 0.0
    try:
        ci = int(draft.get("correct_index"))
        others = [len(o) for i, o in enumerate(options) if i != ci]
        if others:
            gap = abs(len(options[ci]) - sum(others) / len(others))
    except (TypeError, ValueError, IndexError):
        gap = 0.0
    return (fatal, len(flaws), gap)


def _select_best_draft(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    return min(candidates, key=_draft_score) if candidates else None


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
    candidates_per_aspect: int = 1,
) -> list[dict[str, Any]]:
    """One LLM call producing MCQ drafts for the target aspects.

    With ``candidates_per_aspect`` > 1 (best-of-N), the model writes that many
    candidates per aspect and we keep the structurally-strongest one (free
    heuristic scoring) — selection over generation. Returns one normalized draft
    per aspect; the quality gates (verifier, critic, dedup) run on each in the
    caller.
    """
    k = max(1, candidates_per_aspect)
    excerpt = truncate_to_tokens(page_text, _PAGE_EXCERPT_MAX_TOKENS)
    targets_block = "\n".join(
        f"{i + 1}. {t.get('label')} (key: {t.get('key')})"
        + (f" — angle: {t.get('cognitive_angle')}" if t.get("cognitive_angle") else "")
        for i, t in enumerate(targets)
    )
    prior_block = format_prior_mcqs_block(prior_mcqs)
    hints_block = f"\n\nAspect focus hints (variable — not part of cached prefix):\n{aspect_hints}" if aspect_hints else ""

    system = get_prompt(db, "mcq_page_generate_system")
    if k == 1:
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
    else:
        instructions = (
            f"For EACH of the {len(targets)} target aspects below, write {k} DISTINCT candidate "
            f"multiple-choice questions — {len(targets) * k} ```zv-mcq``` blocks total. The candidates "
            f"for one aspect should differ in angle/wording so the best can be chosen.\n"
            f"{_content_style_line(content_type)}\n"
            f"Target aspects:\n{targets_block}\n\n"
            f"{prior_block}"
            f"{hints_block}\n"
            f"Every block's primary_concept_key MUST equal its aspect key — that is how candidates are grouped. "
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

    if k == 1:
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

    # Best-of-N: group candidates by the aspect key the model tagged (robust to
    # ordering/count drift), then keep the strongest candidate per aspect.
    key_to_target = {str(t.get("key")): t for t in targets if t.get("key")}
    by_key: dict[str, list[dict[str, Any]]] = {}
    for data in blocks:
        raw_key = str(data.get("primary_concept_key") or "")
        try:
            norm = _normalize_mcq_payload(data, key_to_target.get(raw_key))
        except ValueError:
            continue
        by_key.setdefault(str(norm.get("primary_concept_key") or raw_key), []).append(norm)

    selected: list[dict[str, Any]] = []
    for t in targets:
        best = _select_best_draft(by_key.get(str(t.get("key")), []))
        if best is not None:
            selected.append(best)
    # Fallback if key-grouping matched nothing (keys drifted): positional best-effort.
    if not selected:
        for idx, data in enumerate(blocks[: len(targets)]):
            try:
                selected.append(_normalize_mcq_payload(data, targets[idx] if idx < len(targets) else None))
            except ValueError:
                continue
    return selected


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
    on_accept: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Generate N MCQs in ONE LLM call, then run quality gates on each.

    ``on_accept(target, draft)`` (if given) is called the moment a draft passes the
    gate — so the caller can persist it immediately and the learner sees the first
    question while the rest are still being critiqued. Returning False stops the
    loop early (e.g. the page budget is full). Dedup is unchanged (still sequential).

    Returns (target_aspect, payload) pairs for MCQs that pass heuristics,
    embedding similarity, and sampled LLM critic checks.
    """
    if not page_text or not page_text.strip() or not targets:
        return []

    targets = targets[:BATCH_MCQ_CAP]
    if model_id is None:
        from app.services.llm_registry import default_chat_model_id

        model_id = default_chat_model_id(db)

    from app.config import get_settings
    from app.services.llm_registry import draft_chat_model_id

    settings = get_settings()
    verify_enabled = settings.verify_answer_key
    # Drafts can use a faster/cheaper model; critic + verifier keep the strong default.
    draft_model_id = draft_chat_model_id(db) or model_id
    candidates_per_aspect = max(1, settings.mcq_candidates_per_aspect)

    # Generation response cache: skip the LLM call if we've already generated
    # drafts for this (page context, aspect set). The quality gate below still
    # runs on every retrieval — only the generation call is skipped. DB-backed
    # so the 4 CPU workers share one cache and a restart doesn't re-pay the LLM.
    from app.services.generation_cache import batch_drafts_key, get as cache_get, put as cache_put

    drafts_cache_key = batch_drafts_key(
        page_number,
        targets,
        prior_mcqs,
        model_id=draft_model_id,
        content_type=content_type,
    )
    drafts = cache_get(db, kind="batch_drafts", cache_key=drafts_cache_key)
    if drafts is None:
        drafts = _generate_batch_drafts(
            db,
            page_text=page_text,
            page_number=page_number,
            targets=targets,
            prior_mcqs=prior_mcqs,
            model_id=draft_model_id,
            aspect_hints=aspect_hints,
            content_type=content_type,
            candidates_per_aspect=candidates_per_aspect,
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
    # Why drafts get discarded, so the gen→accept yield is measurable in prod
    # (a high rejection rate is wasted generation time — the lever we tune).
    rejected: Counter[str] = Counter()
    for idx, draft in enumerate(drafts):
        target = targets[idx] if idx < len(targets) else None
        check_against = list(prior_mcqs or []) + accepted_payloads

        heuristic_flaws = run_heuristic_checks(draft, prior_mcqs=check_against)
        if has_fatal_heuristic_flaws(heuristic_flaws):
            rejected.update(
                f.get("code", "?") for f in heuristic_flaws if f.get("code") in FATAL_FLAW_CODES
            )
            continue

        too_similar, max_sim = is_mcq_too_similar(
            draft,
            check_against,
            prior_embeddings=prior_embeddings,
        )
        if too_similar:
            rejected["too_similar_to_prior"] += 1
            continue

        # Correctness gate: independently re-solve and confirm the marked key.
        # Always-on (cheap, short output) — the one check too important to sample.
        if verify_enabled:
            key_flaw = verify_answer_key(db, mcq=draft, page_text=page_text, model_id=model_id)
            if key_flaw is not None:
                rejected[key_flaw["code"]] += 1
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
                    rejected["rewrite_failed"] += 1
                    continue
                draft = rewritten
                heuristic_flaws = run_heuristic_checks(draft, prior_mcqs=check_against)
                if has_fatal_heuristic_flaws(heuristic_flaws):
                    rejected.update(
                        f.get("code", "?") for f in heuristic_flaws if f.get("code") in FATAL_FLAW_CODES
                    )
                    continue
                too_similar, max_sim = is_mcq_too_similar(
                    draft,
                    check_against,
                    prior_embeddings=prior_embeddings,
                )
                if too_similar:
                    rejected["too_similar_to_prior"] += 1
                    continue
                # The rewrite may have moved the answer — re-verify the key.
                if verify_enabled:
                    key_flaw = verify_answer_key(db, mcq=draft, page_text=page_text, model_id=model_id)
                    if key_flaw is not None:
                        rejected[key_flaw["code"]] += 1
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
                    rejected["critic_rejected"] += 1
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
        if on_accept is not None and on_accept(target, draft) is False:
            break

    logger.info(
        "mcq batch gate: page=%s drafts=%d accepted=%d rejected=%d reasons=%s",
        page_number,
        len(drafts),
        len(accepted),
        sum(rejected.values()),
        dict(rejected),
    )
    return accepted
