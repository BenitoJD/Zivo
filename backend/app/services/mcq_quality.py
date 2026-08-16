"""MCQ quality gate — IWF heuristics + LLM critic + rewrite loop."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import uuid
from collections import Counter
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.llm_router import acomplete_chat
from app.services.presence import evaluate_presence
from app.services.llm_sync import run_coro_in_worker
from app.services.mcq_dedup import (
    embed_signature_cached,
    format_prior_mcqs_block,
    mcq_signature,
    prior_mcq_embeddings,
    SUBJECT_MATTER_PREFIX,
)
from app.services.prompts import get_prompt
from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens
from app.services.mcq_heuristics import find_invented_entity_flaws
from app.services.mcq_parsing import (
    _parse_mcq_json,
    _parse_critic_json,
    _parse_mcq_blocks,
    _normalize_mcq_payload,
    shuffle_mcq_option_order,
)
from app.services.quality_evaluation import (
    BATCH_MCQ_CAP,
    CRITIC_SAMPLE_RATE,
    FATAL_FLAW_CODES,
    GENERATION_CONCURRENCY,
    MAX_GENERATION_ATTEMPTS,
    PIPELINE_DRAFT_SPLIT,
    QualitySignals,
    critique_passes,
    decide_verdict,
    evaluate_parallel_gate_prefilter,
    evaluate_pre_critic_reject,
    has_fatal_heuristic_flaws,
    hard_fail_rewrite_bundle,
    judge_mcq_similarity,
    merge_critique_for_rewrite,
    pick_best_draft,
    plan_best_of_n_candidates,
    plan_cook_gate_knobs,
    plan_cook_gate_schedule,
    plan_rewrite_step,
    run_heuristic_checks,
    should_fast_path_accept,
    should_inject_mcq_content_style,
    should_positional_best_of_n,
    should_run_answer_key_verify,
    should_run_critic,
    should_run_similarity_gate,
)

logger = logging.getLogger(__name__)

_PAGE_EXCERPT_MAX_TOKENS = PAGE_INPUT_MAX_TOKENS

_DISTRACTOR_ENGINE_CODES = (
    "implausible_distractors",
    "none_or_all_of_above",
    "longest_option_correct",
)

_VERIFY_RULES = (
    Rule(when=(Pred("no_parse", "truthy"),), action="parse_fail"),
    Rule(when=(Pred("none_defensible", "truthy"),), action="not_grounded"),
    Rule(when=(Pred("multiple_defensible", "truthy"),), action="multiple"),
    Rule(when=(Pred("bad_index", "truthy"),), action="parse_fail"),
    Rule(when=(Pred("mismatch", "truthy"),), action="wrong_key"),
    Rule(when=(), action="ok"),
)

_VERIFY_MULTI_RULES = (
    Rule(when=(Pred("no_parse", "truthy"),), action="parse_fail"),
    Rule(when=(Pred("none_defensible", "truthy"),), action="not_grounded"),
    Rule(when=(Pred("bad_list", "truthy"),), action="parse_fail"),
    Rule(when=(Pred("empty_set", "truthy"),), action="parse_fail"),
    Rule(when=(Pred("mismatch", "truthy"),), action="wrong_key"),
    Rule(when=(), action="ok"),
)

_DRAFT_SOURCE_RULES = (
    Rule(when=(Pred("cached", "truthy"),), action="cached"),
    Rule(when=(Pred("split", "truthy"),), action="split"),
    Rule(when=(), action="oneshot"),
)

_GATE_WORKERS_RULES = (
    Rule(when=(Pred("serial", "truthy"),), action="serial"),
    Rule(when=(), action="pool"),
)

_UNPARSEABLE_CRITIC = {
    "pass": False,
    "flaw_count": 99,
    "fatal_flaws": ["ambiguous_unclear"],
    "flaws": [{"code": "ambiguous_unclear", "message": "Critic response unparseable"}],
    "rewrite_hints": "Regenerate with a clear stem, one best answer, and plausible distractors.",
}


def _raise(exc: BaseException) -> Any:
    raise exc


def _stripped_options(mcq: dict[str, Any]) -> list[str]:
    return list(filter(None, map(lambda o: str(o).strip(), mcq.get("options") or [])))


def _try_normalize(
    parsed: dict[str, Any], target_aspect: dict[str, Any] | None
) -> dict[str, Any] | None:
    try:
        return _normalize_mcq_payload(parsed, target_aspect)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _blank_page(page_text: str | None) -> bool:
    return evaluate_presence(page_text).action != "ok" or not str(page_text or "").strip()


def _complete_chat_sync(
    db: Session, messages: list[dict], *, log_tag: str, model_id: uuid.UUID | None = None
) -> str:
    try:
        return run_coro_in_worker(
            acomplete_chat(messages, db, log_tag=log_tag, model_id=model_id)
        )
    except Exception as err:
        caught = err
        # Generation pins one model for batch speed; if that model stalls or errors,
        # fall back to the pool (failover) so a single wedged provider can't block
        # question generation entirely.
        return pick(
            model_id is None,
            lambda: _raise(caught),
            lambda: run_coro_in_worker(
                acomplete_chat(messages, db, log_tag=log_tag, model_id=None)
            ),
        )


def _content_style_line(content_type: str | None) -> str:
    """One-line generation directive for the material's content_type.

    Returns '' unless content-aware generation is enabled, so the universal MCQ
    rubric is unchanged by default. When on, it shifts the *kind* of thinking and
    the truth model (e.g. interpretation for narrative) without touching the gate.

    Exception: newspaper_upsc always applies — newspaper editions must frame
    stems like UPSC / Group-1 Prelims regardless of the content_aware flag.
    """
    from app.config import get_settings
    from app.services.prompts import content_type_style

    return pick(
        not should_inject_mcq_content_style(
            content_type,
            content_aware=get_settings().content_aware_generation,
        ),
        lambda: "",
        lambda: choose(
            bool(content_type_style(content_type)),
            f"\nMATERIAL TYPE — {content_type_style(content_type)}\n",
            "",
        ),
    )


# Decision core lives in quality_evaluation (ADR 0004 seam). Thin aliases keep
# existing tests and call sites stable.
_critique_passes = critique_passes
_merge_critique_for_rewrite = merge_critique_for_rewrite


def _aspect_prompt_lines(target_aspect: dict[str, Any]) -> tuple[str, str]:
    angle = (target_aspect.get("cognitive_angle") or "").strip()
    return (
        f"\nTarget this aspect only: {target_aspect.get('label')} "
        f"(key: {target_aspect.get('key')}).\n",
        choose(bool(angle), f"Cognitive angle for this aspect: {angle}.\n", ""),
    )


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
    aspect_line, cognitive_line = pick(
        bool(target_aspect),
        lambda: _aspect_prompt_lines(target_aspect),
        lambda: ("", ""),
    )
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
    return pick(not parsed, lambda: None, lambda: _try_normalize(parsed, target_aspect))


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
    return pick(not parsed, lambda: None, lambda: _try_normalize(parsed, target_aspect))


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
    from app.services.generation_cache import get as cache_get

    options = _stripped_options(mcq)
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
    return pick(
        isinstance(hit, dict) and bool(hit),
        lambda: hit,
        lambda: _critique_uncached(
            db,
            mcq=mcq,
            page_text=page_text,
            target_aspect=target_aspect,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
            critic_key=critic_key,
        ),
    )


def _critique_uncached(
    db: Session,
    *,
    mcq: dict[str, Any],
    page_text: str,
    target_aspect: dict[str, Any] | None,
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None,
    critic_key: str,
) -> dict[str, Any]:
    from app.services.generation_cache import put as cache_put

    excerpt = truncate_to_tokens(page_text, _PAGE_EXCERPT_MAX_TOKENS)
    aspect = target_aspect or {}
    angle = (aspect.get("cognitive_angle") or "").strip()
    cognitive_angle_line = choose(bool(angle), f"Cognitive angle: {angle}.", "")
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
    return pick(
        not parsed,
        lambda: dict(_UNPARSEABLE_CRITIC),
        lambda: (cache_put(db, kind="critic_verdict", cache_key=critic_key, value=parsed), parsed)[1],
    )


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
    options = _stripped_options(mcq)
    marked_indices = mcq.get("correct_indices")
    return pick(
        isinstance(marked_indices, list) and len(marked_indices) >= 2,
        lambda: _verify_answer_key_multi(
            db, mcq=mcq, options=options, page_text=page_text, model_id=model_id
        ),
        lambda: _verify_answer_key_single(
            db, mcq=mcq, options=options, page_text=page_text, model_id=model_id
        ),
    )


def _verify_from_hit(hit: dict[str, Any]) -> dict[str, str] | None:
    return pick(
        hit.get("ok") is True,
        lambda: None,
        lambda: pick(
            isinstance(hit.get("flaw"), dict),
            lambda: hit.get("flaw"),
            lambda: None,
        ),
    )


def _store_verify_verdict(
    db: Session,
    *,
    cache_key: str,
    result: dict[str, str] | None,
    decisive: bool,
) -> dict[str, str] | None:
    from app.services.generation_cache import put as cache_put

    pick(
        decisive,
        lambda: cache_put(
            db,
            kind="verify_verdict",
            cache_key=cache_key,
            value=choose(result is None, {"ok": True}, {"ok": False, "flaw": result}),
        ),
        lambda: None,
    )
    return result


def _verify_answer_key_single(
    db: Session,
    *,
    mcq: dict[str, Any],
    options: list[str],
    page_text: str,
    model_id: uuid.UUID | None,
) -> dict[str, str] | None:
    try:
        marked = int(mcq.get("correct_index"))
    except (TypeError, ValueError):
        return None
    return pick(
        len(options) < 2 or marked < 0 or marked >= len(options),
        lambda: None,
        lambda: _verify_single_lookup(
            db,
            mcq=mcq,
            options=options,
            marked=marked,
            page_text=page_text,
            model_id=model_id,
        ),
    )


def _verify_single_lookup(
    db: Session,
    *,
    mcq: dict[str, Any],
    options: list[str],
    marked: int,
    page_text: str,
    model_id: uuid.UUID | None,
) -> dict[str, str] | None:
    from app.services.chunk_map_cache import verify_verdict_key
    from app.services.generation_cache import get as cache_get

    cache_key = verify_verdict_key(
        stem=str(mcq.get("question") or mcq.get("stem") or ""),
        options=options,
        correct_index=marked,
        page_text=page_text,
        model_id=model_id,
    )
    hit = cache_get(db, kind="verify_verdict", cache_key=cache_key)
    return pick(
        isinstance(hit, dict),
        lambda: _verify_from_hit(hit),
        lambda: _verify_single_miss(
            db,
            mcq=mcq,
            options=options,
            marked=marked,
            page_text=page_text,
            model_id=model_id,
            cache_key=cache_key,
        ),
    )


def _verify_single_miss(
    db: Session,
    *,
    mcq: dict[str, Any],
    options: list[str],
    marked: int,
    page_text: str,
    model_id: uuid.UUID | None,
    cache_key: str,
) -> dict[str, str] | None:
    result, decisive = _verify_answer_key_uncached(
        db, mcq=mcq, options=options, marked=marked, page_text=page_text, model_id=model_id
    )
    return _store_verify_verdict(db, cache_key=cache_key, result=result, decisive=decisive)


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
    independent = pick(
        not parsed,
        lambda: None,
        lambda: _int_or_none(parsed.get("answer_index")),
    )
    action = first_match(
        _VERIFY_RULES,
        {
            "no_parse": not parsed,
            "none_defensible": bool(parsed) and parsed.get("none_defensible") is True,
            "multiple_defensible": bool(parsed) and parsed.get("multiple_defensible") is True,
            "bad_index": bool(parsed)
            and (independent is None or not (0 <= independent < len(options))),
            "mismatch": independent is not None and independent != marked,
        },
    ).action
    return apply(
        action,
        {
            "parse_fail": lambda: (None, False),
            "not_grounded": lambda: (
                {
                    "code": "not_grounded",
                    "message": "Verifier: no option is supported by the source",
                },
                True,
            ),
            "multiple": lambda: (
                {
                    "code": "more_than_one_correct",
                    "message": "Verifier: two or more options are defensible",
                },
                True,
            ),
            "wrong_key": lambda: (
                {
                    "code": "wrong_answer_key",
                    "message": (
                        f"Verifier independently chose option {independent}, not the marked {marked}"
                    ),
                },
                True,
            ),
            "ok": lambda: (None, True),
        },
    )


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
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get

    marked = sorted(
        set(
            filter(
                lambda i: isinstance(i, int) and 0 <= i < len(options),
                mcq.get("correct_indices") or [],
            )
        )
    )
    return pick(
        len(options) < 2 or len(marked) < 2,
        lambda: None,
        lambda: _verify_multi_lookup(
            db,
            mcq=mcq,
            options=options,
            marked=marked,
            page_text=page_text,
            model_id=model_id,
            cache_key=content_hash_key(
                "verify_multi",
                str(mcq.get("question") or mcq.get("stem") or ""),
                "|".join(options),
                ",".join(str(i) for i in marked),
                hashlib.sha256((page_text or "").encode("utf-8", "ignore")).hexdigest()[:32],
                model_id,
            ),
            hit=cache_get(
                db,
                kind="verify_verdict",
                cache_key=content_hash_key(
                    "verify_multi",
                    str(mcq.get("question") or mcq.get("stem") or ""),
                    "|".join(options),
                    ",".join(str(i) for i in marked),
                    hashlib.sha256((page_text or "").encode("utf-8", "ignore")).hexdigest()[:32],
                    model_id,
                ),
            ),
        ),
    )


def _verify_multi_lookup(
    db: Session,
    *,
    mcq: dict[str, Any],
    options: list[str],
    marked: list[int],
    page_text: str,
    model_id: uuid.UUID | None,
    cache_key: str,
    hit: Any,
) -> dict[str, str] | None:
    return pick(
        isinstance(hit, dict),
        lambda: _verify_from_hit(hit),
        lambda: _verify_multi_miss(
            db,
            mcq=mcq,
            options=options,
            marked=marked,
            page_text=page_text,
            model_id=model_id,
            cache_key=cache_key,
        ),
    )


def _verify_multi_miss(
    db: Session,
    *,
    mcq: dict[str, Any],
    options: list[str],
    marked: list[int],
    page_text: str,
    model_id: uuid.UUID | None,
    cache_key: str,
) -> dict[str, str] | None:
    result, decisive = _verify_answer_key_multi_uncached(
        db, mcq=mcq, options=options, marked=marked, page_text=page_text, model_id=model_id
    )
    return _store_verify_verdict(db, cache_key=cache_key, result=result, decisive=decisive)


def _verify_answer_key_multi_uncached(
    db: Session,
    *,
    mcq: dict[str, Any],
    options: list[str],
    marked: list[int],
    page_text: str,
    model_id: uuid.UUID | None = None,
) -> tuple[dict[str, str] | None, bool]:
    """Return (flaw_or_None, decisive). decisive=False → do not cache (parse fail)."""
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
    raw_indices = pick(
        not parsed,
        lambda: None,
        lambda: parsed.get("answer_indices"),
    )
    independent = pick(
        not isinstance(raw_indices, list),
        lambda: [],
        lambda: sorted(
            set(
                map(
                    int,
                    filter(
                        lambda i: isinstance(i, (int, float)) and 0 <= int(i) < len(options),
                        raw_indices,
                    ),
                )
            )
        ),
    )
    action = first_match(
        _VERIFY_MULTI_RULES,
        {
            "no_parse": not parsed,
            "none_defensible": bool(parsed) and parsed.get("none_defensible") is True,
            "bad_list": bool(parsed) and not isinstance(raw_indices, list),
            "empty_set": bool(parsed) and isinstance(raw_indices, list) and not independent,
            "mismatch": bool(independent) and independent != marked,
        },
    ).action
    return apply(
        action,
        {
            "parse_fail": lambda: (None, False),
            "not_grounded": lambda: (
                {
                    "code": "not_grounded",
                    "message": "Verifier: no option is supported by the source",
                },
                True,
            ),
            "wrong_key": lambda: (
                {
                    "code": "wrong_answer_key",
                    "message": (
                        f"Verifier independently chose options {independent}, not the marked {marked}"
                    ),
                },
                True,
            ),
            "ok": lambda: (None, True),
        },
    )


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
    return pick(
        _blank_page(page_text),
        lambda: None,
        lambda: _generate_quality_mcq_loop(
            db,
            page_text=page_text,
            page_number=page_number,
            sequence=sequence,
            target_aspect=target_aspect,
            prior_mcqs=prior_mcqs,
            asked_labels=asked_labels,
            max_attempts=max_attempts,
            model_id=model_id,
            content_type=content_type,
        ),
    )


def _attach_cognitive_angle(
    draft: dict[str, Any], target_aspect: dict[str, Any] | None
) -> dict[str, Any]:
    pick(
        bool(target_aspect and target_aspect.get("cognitive_angle")),
        lambda: draft.__setitem__("cognitive_angle", target_aspect["cognitive_angle"]),
        lambda: None,
    )
    return draft


def _generate_quality_mcq_loop(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    sequence: int,
    target_aspect: dict[str, Any] | None,
    prior_mcqs: list[dict[str, Any]] | None,
    asked_labels: list[str] | None,
    max_attempts: int,
    model_id: uuid.UUID | None,
    content_type: str | None,
) -> dict[str, Any] | None:
    from app.services.llm_registry import default_chat_model_id, draft_chat_model_id

    prior = pick(
        prior_mcqs is None and bool(asked_labels),
        lambda: [
            {"aspect_label": label, "question": "", "correct_answer": ""}
            for label in asked_labels
        ],
        lambda: prior_mcqs,
    )
    pinned = pick(
        model_id is None,
        lambda: default_chat_model_id(db),
        lambda: model_id,
    )
    draft_model_id = draft_chat_model_id(db) or pinned
    state: dict[str, Any] = {
        "draft": None,
        "bundle": {"flaws": [], "rewrite_hints": ""},
        "found": None,
    }

    def run_attempt(attempt: int) -> None:
        pick(
            state["found"] is not None,
            lambda: None,
            lambda: _cook_serial_attempt(
                db,
                attempt=attempt,
                max_attempts=max_attempts,
                page_text=page_text,
                page_number=page_number,
                sequence=sequence,
                target_aspect=target_aspect,
                prior_mcqs=prior,
                model_id=pinned,
                draft_model_id=draft_model_id,
                content_type=content_type,
                state=state,
            ),
        )

    for attempt in range(max_attempts):
        run_attempt(attempt)
    return state["found"]


def _cook_serial_attempt(
    db: Session,
    *,
    attempt: int,
    max_attempts: int,
    page_text: str,
    page_number: int,
    sequence: int,
    target_aspect: dict[str, Any] | None,
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None,
    draft_model_id: uuid.UUID | None,
    content_type: str | None,
    state: dict[str, Any],
) -> None:
    step = plan_rewrite_step(
        attempt=attempt,
        has_draft=state["draft"] is not None,
        has_rewrite_brief=bool(
            state["bundle"].get("flaws") or state["bundle"].get("rewrite_hints")
        ),
    )
    state["draft"] = pick(
        step == "rewrite" and state["draft"] is not None,
        lambda: _rewrite_mcq(
            db,
            draft=state["draft"],
            page_text=page_text,
            page_number=page_number,
            target_aspect=target_aspect,
            critique_bundle=state["bundle"],
            prior_mcqs=prior_mcqs,
            model_id=model_id,
        ),
        lambda: _generate_draft_mcq(
            db,
            page_text=page_text,
            page_number=page_number,
            sequence=sequence,
            target_aspect=target_aspect,
            prior_mcqs=prior_mcqs,
            model_id=draft_model_id,
            content_type=content_type,
        ),
    )
    pick(
        state["draft"] is None,
        lambda: state.__setitem__(
            "bundle",
            {
                "flaws": [{"code": "invalid_structure", "message": "Generation failed"}],
                "rewrite_hints": "Return valid JSON with question, options, correct_index.",
            },
        ),
        lambda: _gate_serial_draft(
            db,
            attempt=attempt,
            max_attempts=max_attempts,
            page_text=page_text,
            page_number=page_number,
            target_aspect=target_aspect,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
            state=state,
        ),
    )


def _gate_serial_draft(
    db: Session,
    *,
    attempt: int,
    max_attempts: int,
    page_text: str,
    page_number: int,
    target_aspect: dict[str, Any] | None,
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None,
    state: dict[str, Any],
) -> None:
    from app.config import get_settings

    draft = state["draft"]
    heuristic_flaws = _enrich_heuristics_with_engines(
        draft, run_heuristic_checks(draft, prior_mcqs=prior_mcqs), page_text=page_text
    )
    key_flaw: dict[str, str] | None = None
    verify_ran = False
    too_similar = False
    max_sim = 0.0
    fatal = has_fatal_heuristic_flaws(heuristic_flaws)

    def run_verify() -> None:
        nonlocal key_flaw, verify_ran
        key_flaw = verify_answer_key(db, mcq=draft, page_text=page_text, model_id=model_id)
        verify_ran = True

    def run_sim() -> None:
        nonlocal too_similar, max_sim
        sim = judge_mcq_similarity(draft, prior_mcqs)
        too_similar, max_sim = sim.too_similar, sim.max_similarity

    pick(
        should_run_answer_key_verify(
            verify_enabled=get_settings().verify_answer_key,
            has_fatal_heuristics=fatal,
            too_similar=False,
        ),
        run_verify,
        lambda: None,
    )
    pick(
        should_run_similarity_gate(has_fatal_heuristics=fatal, verify_flaw=key_flaw),
        run_sim,
        lambda: None,
    )
    rewrite_left = max(0, max_attempts - attempt - 1)
    signals = QualitySignals(
        heuristic_flaws=heuristic_flaws,
        verify_flaw=key_flaw,
        too_similar=too_similar,
        max_similarity=max_sim,
        rewrite_budget_remaining=0,
        verify_ran=verify_ran,
    )
    bundle = hard_fail_rewrite_bundle(signals)
    pick(
        bundle is not None,
        lambda: state.__setitem__("bundle", bundle),
        lambda: _finish_serial_gate(
            db,
            attempt=attempt,
            rewrite_left=rewrite_left,
            page_text=page_text,
            page_number=page_number,
            target_aspect=target_aspect,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
            state=state,
            draft=draft,
            heuristic_flaws=heuristic_flaws,
            key_flaw=key_flaw,
            too_similar=too_similar,
            max_sim=max_sim,
            verify_ran=verify_ran,
            signals=signals,
        ),
    )


def _finish_serial_gate(
    db: Session,
    *,
    attempt: int,
    rewrite_left: int,
    page_text: str,
    page_number: int,
    target_aspect: dict[str, Any] | None,
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None,
    state: dict[str, Any],
    draft: dict[str, Any],
    heuristic_flaws: list[dict[str, Any]],
    key_flaw: dict[str, str] | None,
    too_similar: bool,
    max_sim: float,
    verify_ran: bool,
    signals: QualitySignals,
) -> None:
    hard_fail = decide_verdict(signals)
    pick(
        should_fast_path_accept(signals),
        lambda: state.__setitem__(
            "found",
            shuffle_mcq_option_order(
                _attach_cognitive_angle(
                    _stamp_quality(
                        draft,
                        flaw_count=0,
                        attempts=attempt + 1,
                        extra={
                            "fast_path": True,
                            "heuristic_flaws": [],
                            "max_similarity_to_prior": max_sim,
                            "policy_version": hard_fail.policy_version,
                        },
                        scores=hard_fail.scores,
                    ),
                    target_aspect,
                )
            ),
        ),
        lambda: _critic_serial_gate(
            db,
            attempt=attempt,
            rewrite_left=rewrite_left,
            page_text=page_text,
            page_number=page_number,
            target_aspect=target_aspect,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
            state=state,
            draft=draft,
            heuristic_flaws=heuristic_flaws,
            key_flaw=key_flaw,
            too_similar=too_similar,
            max_sim=max_sim,
            verify_ran=verify_ran,
        ),
    )


def _stamp_quality(
    draft: dict[str, Any],
    *,
    flaw_count: Any,
    attempts: int,
    extra: dict[str, Any],
    scores: Any,
) -> dict[str, Any]:
    quality: dict[str, Any] = {
        "pass": True,
        "flaw_count": flaw_count,
        "attempts": attempts,
        "heuristic_flaws": extra.get("heuristic_flaws", []),
        "max_similarity_to_prior": extra.get("max_similarity_to_prior", 0.0),
        "policy_version": extra.get("policy_version"),
        "scores": {
            "structure": scores.structure,
            "key": scores.key,
            "distractors": scores.distractors,
            "clarity": scores.clarity,
            "overall": scores.overall,
        },
    }
    leftover = dict(
        filter(
            lambda kv: kv[0]
            not in {"heuristic_flaws", "max_similarity_to_prior", "policy_version"},
            extra.items(),
        )
    )
    quality.update(leftover)
    draft["quality"] = quality
    return draft


def _critic_serial_gate(
    db: Session,
    *,
    attempt: int,
    rewrite_left: int,
    page_text: str,
    page_number: int,
    target_aspect: dict[str, Any] | None,
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None,
    state: dict[str, Any],
    draft: dict[str, Any],
    heuristic_flaws: list[dict[str, Any]],
    key_flaw: dict[str, str] | None,
    too_similar: bool,
    max_sim: float,
    verify_ran: bool,
) -> None:
    critique = critique_mcq(
        db,
        mcq=draft,
        page_text=page_text,
        page_number=page_number,
        target_aspect=target_aspect,
        prior_mcqs=prior_mcqs,
        model_id=model_id,
    )
    extra_flaws = list(
        filter(
            lambda f: f.get("code") not in FATAL_FLAW_CODES,
            critique.get("flaws") or [],
        )
    )
    combined_count = (
        len(heuristic_flaws) + len(extra_flaws) + len(critique.get("fatal_flaws") or [])
    )
    critique = {
        **critique,
        "flaw_count": max(int(critique.get("flaw_count") or 0), combined_count),
    }
    verdict = decide_verdict(
        QualitySignals(
            heuristic_flaws=heuristic_flaws,
            verify_flaw=key_flaw,
            critic=critique,
            too_similar=too_similar,
            max_similarity=max_sim,
            rewrite_budget_remaining=rewrite_left,
            verify_ran=verify_ran,
        )
    )
    pick(
        verdict.decision == "pass",
        lambda: state.__setitem__(
            "found",
            shuffle_mcq_option_order(
                _attach_cognitive_angle(
                    _stamp_quality(
                        draft,
                        flaw_count=critique.get("flaw_count", 0),
                        attempts=attempt + 1,
                        extra={
                            "heuristic_flaws": heuristic_flaws,
                            "max_similarity_to_prior": max_sim,
                            "policy_version": verdict.policy_version,
                        },
                        scores=verdict.scores,
                    ),
                    target_aspect,
                )
            ),
        ),
        lambda: state.__setitem__(
            "bundle", merge_critique_for_rewrite(heuristic_flaws, critique)
        ),
    )


# Parallel LLM verify/critic slots within one batch after the first draft lands.
# Quality Evaluation owns the cap + serial-Q1/parallel-rest schedule.


def _select_best_draft(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Best-of-N: Quality Evaluation owns the structural rank."""
    return pick_best_draft(candidates)


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
        + choose(
            bool(t.get("cognitive_angle")),
            f" — angle: {t.get('cognitive_angle')}",
            "",
        )
        for i, t in enumerate(targets)
    )
    prior_block = format_prior_mcqs_block(prior_mcqs)
    hints_block = choose(
        bool(aspect_hints),
        f"\n\nAspect focus hints (variable — not part of cached prefix):\n{aspect_hints}",
        "",
    )

    system = get_prompt(db, "mcq_page_generate_system")
    instructions = pick(
        k == 1,
        lambda: (
            f"Generate exactly {len(targets)} multiple-choice questions — ONE per target aspect below. "
            f"Each must test a distinct idea from the subject matter.\n"
            f"{_content_style_line(content_type)}\n"
            f"Target aspects:\n{targets_block}\n\n"
            f"{prior_block}"
            f"{hints_block}\n"
            f"Return ONLY {len(targets)} ```zv-mcq``` JSON blocks, one per aspect, in order. "
            "Each block's primary_concept_key must match its target aspect key. "
            "Be economical with tokens: no commentary, explanation at most ONE short sentence."
        ),
        lambda: (
            f"For EACH of the {len(targets)} target aspects below, write {k} DISTINCT candidate "
            f"multiple-choice questions — {len(targets) * k} ```zv-mcq``` blocks total. The candidates "
            f"for one aspect should differ in angle/wording so the best can be chosen.\n"
            f"{_content_style_line(content_type)}\n"
            f"Target aspects:\n{targets_block}\n\n"
            f"{prior_block}"
            f"{hints_block}\n"
            f"Every block's primary_concept_key MUST equal its aspect key — that is how candidates are grouped. "
            "Be economical with tokens: no commentary, explanation at most ONE short sentence."
        ),
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
    return pick(
        k == 1,
        lambda: _normalize_batch_positional(blocks, targets),
        lambda: _normalize_batch_best_of_n(blocks, targets),
    )


def _try_normalize_into(
    out: list[dict[str, Any]], data: dict[str, Any], target: dict[str, Any] | None
) -> None:
    try:
        out.append(_normalize_mcq_payload(data, target))
    except ValueError:
        return None


def _normalize_batch_positional(
    blocks: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, data in enumerate(blocks):
        _try_normalize_into(
            normalized,
            data,
            pick(idx < len(targets), lambda: targets[idx], lambda: None),
        )
    return normalized


def _normalize_batch_best_of_n(
    blocks: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    key_to_target = {
        str(t.get("key")): t for t in filter(lambda row: row.get("key"), targets)
    }
    by_key: dict[str, list[dict[str, Any]]] = {}
    for data in blocks:
        raw_key = str(data.get("primary_concept_key") or "")
        norm = _try_normalize(data, key_to_target.get(raw_key))
        pick(
            norm is not None,
            lambda n=norm, k=raw_key: by_key.setdefault(
                str(n.get("primary_concept_key") or k), []
            ).append(n),
            lambda: None,
        )
    selected: list[dict[str, Any]] = []
    for t in targets:
        best = _select_best_draft(by_key.get(str(t.get("key")), []))
        pick(best is not None, lambda b=best: selected.append(b), lambda: None)

    def positional_fallback() -> None:
        for idx, data in enumerate(blocks[: len(targets)]):
            _try_normalize_into(
                selected,
                data,
                pick(idx < len(targets), lambda: targets[idx], lambda: None),
            )

    pick(
        should_positional_best_of_n(selected_count=len(selected)),
        positional_fallback,
        lambda: None,
    )
    return selected


def _enrich_heuristics_with_engines(
    draft: dict[str, Any],
    heuristic_flaws: list[dict[str, Any]],
    *,
    page_text: str,
) -> list[dict[str, Any]]:
    """Compose Grounding + Distractor engines onto heuristic flaws (all cook paths)."""
    from app.services.grounding_answerability import evaluate_grounding_for_cook
    from app.services.misconception_distractor import evaluate_distractors

    flaws = list(heuristic_flaws)
    options = [str(o) for o in (draft.get("options") or [])]
    correct_idx = draft.get("correct_indices") or pick(
        draft.get("correct_index") is not None,
        lambda: [draft["correct_index"]],
        lambda: [],
    )
    correct_texts = [
        options[i]
        for i in filter(
            lambda i: isinstance(i, int) and 0 <= i < len(options), correct_idx
        )
    ]
    ground = evaluate_grounding_for_cook(
        stem=str(draft.get("question") or draft.get("stem") or ""),
        correct_texts=correct_texts,
        page_text=page_text,
    )

    def append_ground() -> None:
        code = ground.flaw_codes[0]
        pick(
            not any(f.get("code") == code for f in flaws),
            lambda: flaws.append(
                {
                    "code": code,
                    "detail": ground.details or f"grounding_score={ground.score:.3f}",
                }
            ),
            lambda: None,
        )

    pick(ground.fatal and bool(ground.flaw_codes), append_ground, lambda: None)
    for invented in find_invented_entity_flaws(draft, page_text):
        pick(
            not any(f.get("code") == invented.get("code") for f in flaws),
            lambda row=invented: flaws.append(row),
            lambda: None,
        )
    distract = evaluate_distractors(
        options,
        [int(i) for i in filter(lambda i: isinstance(i, int), correct_idx)],
        heuristic_codes=[str(f.get("code") or "") for f in flaws],
    )
    for code in distract.flaw_codes:
        pick(
            code in _DISTRACTOR_ENGINE_CODES
            and not any(f.get("code") == code for f in flaws),
            lambda c=code: flaws.append({"code": c, "detail": "distractor_engine"}),
            lambda: None,
        )
    return flaws


def _quality_gate_one(
    db: Session,
    *,
    draft: dict[str, Any],
    target: dict[str, Any] | None,
    check_against: list[dict[str, Any]],
    prior_embeddings: list[Any],
    page_text: str,
    page_number: int,
    model_id: uuid.UUID | None,
    verify_enabled: bool,
    force_critic: bool,
) -> tuple[str, dict[str, Any] | None, str | None, float, list[dict[str, Any]], dict[str, Any] | None, bool]:
    """Run heuristic → embed → verify → critic(+rewrite) for one draft.

    Quality decisions go through ``quality_evaluation.decide_verdict`` (policy seam).
    Returns ``(status, draft|None, reject_reason|None, max_sim, heuristic_flaws,
    critic_meta|None, run_critic)``. status is ``accept`` or ``reject``.
    """
    heuristic_flaws = _enrich_heuristics_with_engines(
        draft, run_heuristic_checks(draft, prior_mcqs=check_against), page_text=page_text
    )
    st: dict[str, Any] = {
        "draft": draft,
        "heuristic_flaws": heuristic_flaws,
        "too_similar": False,
        "max_sim": 0.0,
        "key_flaw": None,
        "verify_ran": False,
        "critic_meta": None,
        "run_critic": False,
        "result": None,
    }
    fatal = has_fatal_heuristic_flaws(heuristic_flaws)

    def run_sim() -> None:
        sim = judge_mcq_similarity(
            st["draft"],
            check_against,
            prior_embeddings=prior_embeddings,
        )
        st["too_similar"], st["max_sim"] = sim.too_similar, sim.max_similarity

    def run_verify() -> None:
        st["key_flaw"] = verify_answer_key(
            db, mcq=st["draft"], page_text=page_text, model_id=model_id
        )
        st["verify_ran"] = True

    pick(
        should_run_similarity_gate(has_fatal_heuristics=fatal, verify_flaw=None),
        run_sim,
        lambda: None,
    )
    pick(
        should_run_answer_key_verify(
            verify_enabled=verify_enabled,
            has_fatal_heuristics=fatal,
            too_similar=st["too_similar"],
        ),
        run_verify,
        lambda: None,
    )
    early = evaluate_pre_critic_reject(
        QualitySignals(
            heuristic_flaws=st["heuristic_flaws"],
            verify_flaw=st["key_flaw"],
            too_similar=st["too_similar"],
            max_similarity=st["max_sim"],
            rewrite_budget_remaining=0,
            verify_ran=st["verify_ran"],
        )
    )
    pick(
        early is not None,
        lambda: st.__setitem__(
            "result",
            (
                "reject",
                None,
                early.reject_reason or "heuristic",
                st["max_sim"],
                st["heuristic_flaws"],
                None,
                False,
            ),
        ),
        lambda: _quality_gate_after_early(
            db,
            st=st,
            target=target,
            check_against=check_against,
            prior_embeddings=prior_embeddings,
            page_text=page_text,
            page_number=page_number,
            model_id=model_id,
            verify_enabled=verify_enabled,
            force_critic=force_critic,
        ),
    )
    return st["result"]


def _gate_accept(st: dict[str, Any]) -> None:
    st["result"] = (
        "accept",
        st["draft"],
        None,
        st["max_sim"],
        st["heuristic_flaws"],
        st["critic_meta"],
        st["run_critic"],
    )


def _gate_reject(st: dict[str, Any], reason: str, run_critic: bool) -> None:
    st["result"] = (
        "reject",
        None,
        reason,
        st["max_sim"],
        st["heuristic_flaws"],
        st["critic_meta"],
        run_critic,
    )


def _quality_gate_after_early(
    db: Session,
    *,
    st: dict[str, Any],
    target: dict[str, Any] | None,
    check_against: list[dict[str, Any]],
    prior_embeddings: list[Any],
    page_text: str,
    page_number: int,
    model_id: uuid.UUID | None,
    verify_enabled: bool,
    force_critic: bool,
) -> None:
    st["run_critic"] = should_run_critic(
        force_critic=force_critic,
        heuristic_flaws=st["heuristic_flaws"],
        sample_roll=random.random(),
        sample_rate=CRITIC_SAMPLE_RATE,
    )
    pick(
        st["run_critic"],
        lambda: _quality_gate_critic(
            db,
            st=st,
            target=target,
            check_against=check_against,
            prior_embeddings=prior_embeddings,
            page_text=page_text,
            page_number=page_number,
            model_id=model_id,
            verify_enabled=verify_enabled,
        ),
        lambda: _gate_accept(st),
    )


def _quality_gate_critic(
    db: Session,
    *,
    st: dict[str, Any],
    target: dict[str, Any] | None,
    check_against: list[dict[str, Any]],
    prior_embeddings: list[Any],
    page_text: str,
    page_number: int,
    model_id: uuid.UUID | None,
    verify_enabled: bool,
) -> None:
    critique = critique_mcq(
        db,
        mcq=st["draft"],
        page_text=page_text,
        page_number=page_number,
        target_aspect=target,
        prior_mcqs=check_against,
        model_id=model_id,
    )
    st["critic_meta"] = critique
    verdict = decide_verdict(
        QualitySignals(
            heuristic_flaws=st["heuristic_flaws"],
            verify_flaw=st["key_flaw"],
            critic=critique,
            too_similar=st["too_similar"],
            max_similarity=st["max_sim"],
            rewrite_budget_remaining=1,
            verify_ran=st["verify_ran"],
        )
    )
    pick(
        verdict.decision == "revise",
        lambda: _quality_gate_rewrite(
            db,
            st=st,
            critique=critique,
            target=target,
            check_against=check_against,
            prior_embeddings=prior_embeddings,
            page_text=page_text,
            page_number=page_number,
            model_id=model_id,
            verify_enabled=verify_enabled,
        ),
        lambda: _quality_gate_after_verdict(st, verdict),
    )


def _quality_gate_rewrite(
    db: Session,
    *,
    st: dict[str, Any],
    critique: dict[str, Any],
    target: dict[str, Any] | None,
    check_against: list[dict[str, Any]],
    prior_embeddings: list[Any],
    page_text: str,
    page_number: int,
    model_id: uuid.UUID | None,
    verify_enabled: bool,
) -> None:
    rewritten = _rewrite_mcq(
        db,
        draft=st["draft"],
        page_text=page_text,
        page_number=page_number,
        target_aspect=target,
        critique_bundle=merge_critique_for_rewrite(st["heuristic_flaws"], critique),
        prior_mcqs=check_against,
        model_id=model_id,
    )
    pick(
        not rewritten,
        lambda: _gate_reject(st, "rewrite_failed", True),
        lambda: _quality_gate_after_rewrite(
            db,
            st=st,
            rewritten=rewritten,
            target=target,
            check_against=check_against,
            prior_embeddings=prior_embeddings,
            page_text=page_text,
            page_number=page_number,
            model_id=model_id,
            verify_enabled=verify_enabled,
        ),
    )


def _quality_gate_after_rewrite(
    db: Session,
    *,
    st: dict[str, Any],
    rewritten: dict[str, Any],
    target: dict[str, Any] | None,
    check_against: list[dict[str, Any]],
    prior_embeddings: list[Any],
    page_text: str,
    page_number: int,
    model_id: uuid.UUID | None,
    verify_enabled: bool,
) -> None:
    st["draft"] = rewritten
    st["heuristic_flaws"] = _enrich_heuristics_with_engines(
        rewritten,
        run_heuristic_checks(rewritten, prior_mcqs=check_against),
        page_text=page_text,
    )
    sim = judge_mcq_similarity(
        rewritten,
        check_against,
        prior_embeddings=prior_embeddings,
    )
    st["too_similar"], st["max_sim"] = sim.too_similar, sim.max_similarity
    st["key_flaw"] = None
    st["verify_ran"] = False
    pick(
        verify_enabled
        and not has_fatal_heuristic_flaws(st["heuristic_flaws"])
        and not st["too_similar"],
        lambda: (
            st.__setitem__(
                "key_flaw",
                verify_answer_key(db, mcq=rewritten, page_text=page_text, model_id=model_id),
            ),
            st.__setitem__("verify_ran", True),
        ),
        lambda: None,
    )
    critique = critique_mcq(
        db,
        mcq=rewritten,
        page_text=page_text,
        page_number=page_number,
        target_aspect=target,
        prior_mcqs=check_against,
        model_id=model_id,
    )
    st["critic_meta"] = critique
    verdict = decide_verdict(
        QualitySignals(
            heuristic_flaws=st["heuristic_flaws"],
            verify_flaw=st["key_flaw"],
            critic=critique,
            too_similar=st["too_similar"],
            max_similarity=st["max_sim"],
            rewrite_budget_remaining=0,
            verify_ran=st["verify_ran"],
        )
    )
    _quality_gate_after_verdict(st, verdict)


def _quality_gate_after_verdict(st: dict[str, Any], verdict: Any) -> None:
    pick(
        verdict.decision != "pass",
        lambda: _gate_reject(st, verdict.reject_reason or "critic_rejected", True),
        lambda: _gate_accept(st),
    )


def _draft_batch_worker(
    *,
    page_text: str,
    page_number: int,
    targets: list[dict[str, Any]],
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None,
    aspect_hints: str,
    content_type: str | None,
    candidates_per_aspect: int,
) -> list[dict[str, Any]]:
    """Thread worker: draft targets on a dedicated DB session."""
    from app.db import SessionLocal

    with SessionLocal() as session:
        return _generate_batch_drafts(
            session,
            page_text=page_text,
            page_number=page_number,
            targets=targets,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
            aspect_hints=aspect_hints,
            content_type=content_type,
            candidates_per_aspect=candidates_per_aspect,
        )


def _parallel_gate_draft(
    *,
    idx: int,
    draft: dict[str, Any],
    target: dict[str, Any] | None,
    check_against: list[dict[str, Any]],
    prior_embeddings: list[Any],
    page_text: str,
    page_number: int,
    model_id: uuid.UUID | None,
    verify_enabled: bool,
    force_critic: bool,
) -> tuple[int, tuple[str, dict[str, Any] | None, str | None, float, list[dict[str, Any]], dict[str, Any] | None, bool]]:
    """Thread worker: own DB session so SQLAlchemy stays single-threaded per conn."""
    from app.db import SessionLocal

    with SessionLocal() as session:
        result = _quality_gate_one(
            session,
            draft=draft,
            target=target,
            check_against=check_against,
            prior_embeddings=prior_embeddings,
            page_text=page_text,
            page_number=page_number,
            model_id=model_id,
            verify_enabled=verify_enabled,
            force_critic=force_critic,
        )
    return (idx, result)


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
    """Generate N MCQs, quality-gate them, stream accepts via ``on_accept``.

    Pipeline for seamless Learn:
      1. Prefer cache hit for the full target set.
      2. Else (multi-target + ``PIPELINE_DRAFT_SPLIT``): draft target[0] here while
         a worker drafts the rest — gate Q1 as soon as its draft lands.
      3. Else: one shared draft call for all targets.
      4. Gate draft[0] serially (force critic) → ``on_accept`` so Learn unblocks.
      5. Gate remaining drafts in parallel (``GENERATION_CONCURRENCY``, bounded
         by process-wide ``LLM_MAX_CONCURRENT``) while the learner studies.
      6. Finalize accepts in index order with intra-batch dedup.

    Returns (target_aspect, payload) pairs for MCQs that pass heuristics,
    embedding similarity, and sampled LLM critic checks.
    """
    return pick(
        _blank_page(page_text) or not targets,
        lambda: [],
        lambda: _run_quality_mcq_batch(
            db,
            page_text=page_text,
            page_number=page_number,
            targets=targets,
            prior_mcqs=prior_mcqs,
            model_id=model_id,
            aspect_hints=aspect_hints,
            content_type=content_type,
            on_accept=on_accept,
        ),
    )


def _run_quality_mcq_batch(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    targets: list[dict[str, Any]],
    prior_mcqs: list[dict[str, Any]] | None,
    model_id: uuid.UUID | None,
    aspect_hints: str,
    content_type: str | None,
    on_accept: Callable[[dict[str, Any], dict[str, Any]], bool] | None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    from app.config import get_settings
    from app.services.generation_cache import batch_drafts_key, get as cache_get, put as cache_put
    from app.services.llm_registry import default_chat_model_id, draft_chat_model_id

    knobs = plan_cook_gate_knobs(
        generation_concurrency=GENERATION_CONCURRENCY,
        pipeline_split=PIPELINE_DRAFT_SPLIT,
        critic_sample_rate=CRITIC_SAMPLE_RATE,
    )
    schedule = plan_cook_gate_schedule(
        target_count=len(targets),
        pipeline_split=knobs.pipeline_draft_split,
        generation_concurrency=knobs.generation_concurrency,
        batch_cap=BATCH_MCQ_CAP,
    )
    targets = targets[: schedule.batch_cap]
    pinned = pick(
        model_id is None,
        lambda: default_chat_model_id(db),
        lambda: model_id,
    )
    settings = get_settings()
    verify_enabled = settings.verify_answer_key
    draft_model_id = draft_chat_model_id(db) or pinned
    candidates_per_aspect = plan_best_of_n_candidates(settings.mcq_candidates_per_aspect)
    drafts_cache_key = batch_drafts_key(
        page_number,
        targets,
        prior_mcqs,
        model_id=draft_model_id,
        content_type=content_type,
        page_text=page_text,
    )
    ctx: dict[str, Any] = {
        "drafts": cache_get(db, kind="batch_drafts", cache_key=drafts_cache_key),
        "accepted": [],
        "accepted_payloads": [],
        "prior_embeddings": prior_mcq_embeddings(list(prior_mcqs or [])),
        "rejected": Counter(),
        "stop": False,
        "first_gated": False,
        "targets": targets,
        "schedule": schedule,
        "drafts_cache_key": drafts_cache_key,
        "cache_put": cache_put,
        "db": db,
        "page_text": page_text,
        "page_number": page_number,
        "prior_mcqs": prior_mcqs,
        "model_id": pinned,
        "verify_enabled": verify_enabled,
        "on_accept": on_accept,
        "draft_kwargs": dict(
            page_text=page_text,
            page_number=page_number,
            prior_mcqs=prior_mcqs,
            model_id=draft_model_id,
            aspect_hints=aspect_hints,
            content_type=content_type,
            candidates_per_aspect=candidates_per_aspect,
        ),
    }
    apply(
        first_match(
            _DRAFT_SOURCE_RULES,
            {
                "cached": ctx["drafts"] is not None,
                "split": schedule.split_first_draft,
            },
        ).action,
        {
            "cached": lambda: None,
            "split": lambda: _batch_load_split(ctx),
            "oneshot": lambda: _batch_load_oneshot(ctx),
        },
    )
    pick(not ctx["drafts"], lambda: None, lambda: _batch_gate_drafts(ctx))
    logger.info(
        "mcq batch gate: page=%s drafts=%d accepted=%d rejected=%d reasons=%s concurrency=%s pipeline_split=%s",
        page_number,
        len(ctx["drafts"] or []),
        len(ctx["accepted"]),
        sum(ctx["rejected"].values()),
        dict(ctx["rejected"]),
        schedule.rest_concurrency,
        schedule.split_first_draft,
    )
    return ctx["accepted"]


def _batch_finalize_accept(ctx: dict[str, Any], target: dict[str, Any] | None, draft: dict[str, Any], *, max_sim: float, heuristic_flaws: list[dict[str, Any]], critic_meta: dict[str, Any] | None, run_critic: bool) -> bool:
    draft = shuffle_mcq_option_order(draft)
    draft["quality"] = {
        "pass": True,
        "flaw_count": len(heuristic_flaws),
        "attempts": choose(run_critic and bool(critic_meta), 2, 1),
        "fast_path": not run_critic,
        "batch": True,
        "critic_sampled": run_critic,
        "heuristic_flaws": heuristic_flaws,
        "max_similarity_to_prior": max_sim,
    }
    pick(
        bool(critic_meta),
        lambda: draft["quality"].__setitem__("critic_sampled", True),
        lambda: None,
    )
    _attach_cognitive_angle(draft, target)
    ctx["accepted"].append((target, draft))
    ctx["accepted_payloads"].append(draft)
    ctx["prior_embeddings"].append(embed_signature_cached(mcq_signature(draft)))
    on_accept = ctx["on_accept"]
    return pick(
        on_accept is not None and on_accept(target, draft) is False,
        lambda: False,
        lambda: True,
    )


def _batch_gate_first(ctx: dict[str, Any], draft: dict[str, Any]) -> None:
    ctx["first_gated"] = True
    first_target = pick(bool(ctx["targets"]), lambda: ctx["targets"][0], lambda: None)
    status, gated, reason, max_sim, h_flaws, critic_meta, run_critic = _quality_gate_one(
        ctx["db"],
        draft=draft,
        target=first_target,
        check_against=list(ctx["prior_mcqs"] or []),
        prior_embeddings=list(ctx["prior_embeddings"]),
        page_text=ctx["page_text"],
        page_number=ctx["page_number"],
        model_id=ctx["model_id"],
        verify_enabled=ctx["verify_enabled"],
        force_critic=ctx["schedule"].first_force_critic,
    )
    pick(
        status == "accept" and gated is not None,
        lambda: pick(
            not _batch_finalize_accept(
                ctx,
                first_target,
                gated,
                max_sim=max_sim,
                heuristic_flaws=h_flaws,
                critic_meta=critic_meta,
                run_critic=run_critic,
            ),
            lambda: ctx.__setitem__("stop", True),
            lambda: None,
        ),
        lambda: pick(
            bool(reason),
            lambda: ctx["rejected"].update([reason]),
            lambda: None,
        ),
    )


def _batch_load_split(ctx: dict[str, Any]) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcq-draft") as draft_pool:
        rest_fut = draft_pool.submit(
            _draft_batch_worker,
            targets=ctx["targets"][1:],
            **ctx["draft_kwargs"],
        )
        first_drafts = _generate_batch_drafts(
            ctx["db"], targets=ctx["targets"][:1], **ctx["draft_kwargs"]
        )
        pick(bool(first_drafts), lambda: _batch_gate_first(ctx, first_drafts[0]), lambda: None)
        rest_drafts = rest_fut.result() or []
    ctx["drafts"] = list(first_drafts or []) + list(rest_drafts)
    pick(
        bool(ctx["drafts"]),
        lambda: ctx["cache_put"](
            ctx["db"],
            kind="batch_drafts",
            cache_key=ctx["drafts_cache_key"],
            value=ctx["drafts"],
        ),
        lambda: None,
    )


def _batch_load_oneshot(ctx: dict[str, Any]) -> None:
    ctx["drafts"] = _generate_batch_drafts(
        ctx["db"], targets=ctx["targets"], **ctx["draft_kwargs"]
    )
    pick(
        bool(ctx["drafts"]),
        lambda: ctx["cache_put"](
            ctx["db"],
            kind="batch_drafts",
            cache_key=ctx["drafts_cache_key"],
            value=ctx["drafts"],
        ),
        lambda: None,
    )


def _batch_gate_drafts(ctx: dict[str, Any]) -> None:
    pick(ctx["first_gated"], lambda: None, lambda: _batch_gate_first(ctx, ctx["drafts"][0]))
    rest = list(enumerate(ctx["drafts"][1:], start=1))
    pick(ctx["stop"] or not rest, lambda: None, lambda: _batch_gate_rest(ctx, rest))


def _batch_gate_rest(ctx: dict[str, Any], rest: list[tuple[int, dict[str, Any]]]) -> None:
    prior_only = list(ctx["prior_mcqs"] or [])
    prior_only_embeddings = prior_mcq_embeddings(prior_only)
    work: list[tuple[int, dict[str, Any], dict[str, Any] | None]] = []
    for idx, draft in rest:
        _batch_consider_rest_draft(
            ctx,
            work,
            idx,
            draft,
            prior_only,
            prior_only_embeddings,
        )
    gated_by_idx: dict[int, Any] = {}
    pick(
        not work,
        lambda: None,
        lambda: apply(
            first_match(
                _GATE_WORKERS_RULES,
                {"serial": min(ctx["schedule"].rest_concurrency, len(work)) <= 1},
            ).action,
            {
                "serial": lambda: _batch_gate_serial(
                    ctx, work, prior_only, prior_only_embeddings, gated_by_idx
                ),
                "pool": lambda: _batch_gate_pool(
                    ctx, work, prior_only, prior_only_embeddings, gated_by_idx
                ),
            },
        ),
    )
    for idx in sorted(gated_by_idx):
        pick(
            ctx["stop"],
            lambda: None,
            lambda i=idx: _batch_finalize_idx(ctx, i, gated_by_idx[i]),
        )


def _batch_consider_rest_draft(
    ctx: dict[str, Any],
    work: list[tuple[int, dict[str, Any], dict[str, Any] | None]],
    idx: int,
    draft: dict[str, Any],
    prior_only: list[dict[str, Any]],
    prior_only_embeddings: list[Any],
) -> None:
    target = pick(idx < len(ctx["targets"]), lambda: ctx["targets"][idx], lambda: None)
    h = run_heuristic_checks(draft, prior_mcqs=prior_only)
    apply(
        evaluate_parallel_gate_prefilter(heuristic_flaws=h, too_similar=False),
        {
            "fatal": lambda: ctx["rejected"].update(
                map(
                    lambda f: f.get("code", "?"),
                    filter(lambda f: f.get("code") in FATAL_FLAW_CODES, h),
                )
            ),
            "too_similar": lambda: None,
            "keep": lambda: _batch_consider_similarity(
                ctx, work, idx, draft, target, h, prior_only, prior_only_embeddings
            ),
        },
    )


def _batch_consider_similarity(
    ctx: dict[str, Any],
    work: list[tuple[int, dict[str, Any], dict[str, Any] | None]],
    idx: int,
    draft: dict[str, Any],
    target: dict[str, Any] | None,
    h: list[dict[str, Any]],
    prior_only: list[dict[str, Any]],
    prior_only_embeddings: list[Any],
) -> None:
    too_sim = judge_mcq_similarity(
        draft, prior_only, prior_embeddings=prior_only_embeddings
    ).too_similar
    apply(
        evaluate_parallel_gate_prefilter(heuristic_flaws=h, too_similar=too_sim),
        {
            "fatal": lambda: ctx["rejected"].update(
                map(
                    lambda f: f.get("code", "?"),
                    filter(lambda f: f.get("code") in FATAL_FLAW_CODES, h),
                )
            ),
            "too_similar": lambda: ctx["rejected"].update(["too_similar_to_prior"]),
            "keep": lambda: work.append((idx, draft, target)),
        },
    )


def _batch_gate_serial(
    ctx: dict[str, Any],
    work: list[tuple[int, dict[str, Any], dict[str, Any] | None]],
    prior_only: list[dict[str, Any]],
    prior_only_embeddings: list[Any],
    gated_by_idx: dict[int, Any],
) -> None:
    for idx, draft, target in work:
        gated_by_idx[idx] = _quality_gate_one(
            ctx["db"],
            draft=draft,
            target=target,
            check_against=prior_only,
            prior_embeddings=list(prior_only_embeddings),
            page_text=ctx["page_text"],
            page_number=ctx["page_number"],
            model_id=ctx["model_id"],
            verify_enabled=ctx["verify_enabled"],
            force_critic=ctx["schedule"].rest_force_critic,
        )


def _batch_gate_pool(
    ctx: dict[str, Any],
    work: list[tuple[int, dict[str, Any], dict[str, Any] | None]],
    prior_only: list[dict[str, Any]],
    prior_only_embeddings: list[Any],
    gated_by_idx: dict[int, Any],
) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = min(ctx["schedule"].rest_concurrency, len(work))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mcq-gate") as pool:
        futures = [
            pool.submit(
                _parallel_gate_draft,
                idx=idx,
                draft=draft,
                target=target,
                check_against=prior_only,
                prior_embeddings=list(prior_only_embeddings),
                page_text=ctx["page_text"],
                page_number=ctx["page_number"],
                model_id=ctx["model_id"],
                verify_enabled=ctx["verify_enabled"],
                force_critic=ctx["schedule"].rest_force_critic,
            )
            for idx, draft, target in work
        ]
        for fut in as_completed(futures):
            idx, result = fut.result()
            gated_by_idx[idx] = result


def _batch_finalize_idx(ctx: dict[str, Any], idx: int, gated_row: Any) -> None:
    status, gated, reason, _max_sim, h_flaws, critic_meta, run_critic = gated_row
    pick(
        status != "accept" or gated is None,
        lambda: pick(
            bool(reason),
            lambda: ctx["rejected"].update([reason]),
            lambda: None,
        ),
        lambda: _batch_finalize_accepted(ctx, idx, gated, h_flaws, critic_meta, run_critic),
    )


def _batch_finalize_accepted(
    ctx: dict[str, Any],
    idx: int,
    gated: dict[str, Any],
    h_flaws: list[dict[str, Any]],
    critic_meta: dict[str, Any] | None,
    run_critic: bool,
) -> None:
    check_against = list(ctx["prior_mcqs"] or []) + ctx["accepted_payloads"]
    sim2 = judge_mcq_similarity(
        gated,
        check_against,
        prior_embeddings=ctx["prior_embeddings"],
    )
    pick(
        sim2.too_similar,
        lambda: ctx["rejected"].update(["too_similar_to_prior"]),
        lambda: pick(
            not _batch_finalize_accept(
                ctx,
                pick(idx < len(ctx["targets"]), lambda: ctx["targets"][idx], lambda: None),
                gated,
                max_sim=sim2.max_similarity,
                heuristic_flaws=h_flaws,
                critic_meta=critic_meta,
                run_critic=run_critic,
            ),
            lambda: ctx.__setitem__("stop", True),
            lambda: None,
        ),
    )
