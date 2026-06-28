"""MCQ quality gate — IWF heuristics + LLM critic + rewrite loop."""

from __future__ import annotations

import json
import os
import random
import uuid
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
        if on_accept is not None and on_accept(target, draft) is False:
            break

    return accepted
