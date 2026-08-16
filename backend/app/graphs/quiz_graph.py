"""Quiz/worksheet generation (worker-only) — the Question Generator feature.

Turns a source into a structured set of questions of the educator's chosen types
(MCQ, multi-select MCQ, true/false, fill-in-the-blank, short answer, essay, matching)
at a chosen difficulty, each with an answer key, so it can be previewed and exported
as a worksheet. Mirrors notes_graph's map-reduce over the document's RAG chunks (no new
ingest); off the answer path in a background worker.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.services.chunks import load_document_chunk_texts
from app.services.llm_json import extract_json_array
from app.services.llm_route import evaluate_llm_route
from app.services.llm_router import complete_chat
from app.services.presence import evaluate_presence
from app.services.prompts import get_prompt
from app.services.session_design import clamp_auxiliary_count, plan_auxiliary_field_caps
from app.services.token_budget import (
    SUMMARIZE_SINGLE_SHOT_MAX_TOKENS,
    truncate_to_tokens,
)

# Supported question types → human label used in the prompt + export.
QUESTION_TYPES = {
    "mcq": "Multiple choice (one correct answer)",
    "multi": "Multiple choice (one or more correct answers)",
    "mcq_negative": "Negative / EXCEPT multiple choice (one correct answer)",
    "assertion_reason": "Assertion–Reason multiple choice (one correct answer)",
    "scenario": "Scenario / case-based multiple choice (one correct answer)",
    "cloze": "Cloze / fill-in-the-blank multiple choice (one correct answer)",
    "truefalse": "True / False",
    "fill_blank": "Fill in the blank",
    "short": "Short answer",
    "essay": "Long answer / essay",
    "matching": "Matching",
}
# MCQ-variant styles that share the plain `mcq` payload schema
# (prompt, options, answer_index, explanation) so they validate, render,
# and export through the same single-best-answer code path.
SINGLE_ANSWER_MCQ_TYPES = ("mcq", "mcq_negative", "assertion_reason", "scenario", "cloze")

_QTYPE_RULES = (
    Rule(when=(Pred("qtype", "eq", "multi"),), action="multi"),
    Rule(when=(Pred("qtype", "in_set", SINGLE_ANSWER_MCQ_TYPES),), action="single_mcq"),
    Rule(when=(Pred("qtype", "eq", "truefalse"),), action="truefalse"),
    Rule(when=(Pred("qtype", "in_set", ("fill_blank", "short", "essay")),), action="text"),
    Rule(when=(Pred("qtype", "eq", "matching"),), action="matching"),
    Rule(when=(), action="drop"),
)


def _parse_questions(raw: str) -> list[dict]:
    """Extract a JSON array of question objects (tolerant of fences/prose/truncation)."""
    return extract_json_array(raw)


def _norm_options(item: dict) -> list[str]:
    opts = item.get("options") or item.get("choices") or []
    cap = plan_auxiliary_field_caps("quiz").limit("options")
    cleaned = [str(o).strip() for o in filter(lambda o: str(o).strip(), opts)]
    return cleaned[:cap]


def _drop() -> None:
    return None


def _shape_question(item: dict, allowed: set[str], fields: Any) -> dict | None:
    qtype = str(item.get("type") or "").strip().lower()
    prompt = str(item.get("prompt") or item.get("question") or "").strip()
    expl = str(item.get("explanation") or "").strip()[: fields.limit("explanation")]
    q: dict = {
        "type": qtype,
        "prompt": prompt[: fields.limit("prompt")],
        "explanation": expl,
    }

    def _single_mcq() -> dict | None:
        opts = _norm_options(item)

        def _with_idx() -> dict | None:
            idx = item.get("answer_index")
            return pick(
                isinstance(idx, int) and 0 <= idx < len(opts),
                lambda: {**q, "options": opts, "answer_index": idx},
                _drop,
            )

        return pick(len(opts) < 2, _drop, _with_idx)

    def _multi() -> dict | None:
        opts = _norm_options(item)
        idxs = item.get("answer_indices") or []
        idxs = list(
            filter(lambda i: isinstance(i, int) and 0 <= i < len(opts), idxs)
        )

        def _with_idxs() -> dict:
            return {**q, "options": opts, "answer_indices": sorted(set(idxs))}

        return pick(len(opts) < 2 or not idxs, _drop, _with_idxs)

    def _truefalse() -> dict | None:
        ans = item.get("answer")
        ans = pick(
            isinstance(ans, str),
            lambda: str(ans).strip().lower() in ("true", "t", "yes"),
            lambda: ans,
        )
        return pick(isinstance(ans, bool), lambda: {**q, "answer": ans}, _drop)

    def _text() -> dict | None:
        ans = str(item.get("answer") or "").strip()
        return pick(
            not ans,
            _drop,
            lambda: {**q, "answer": ans[: fields.limit("answer")]},
        )

    def _matching() -> dict | None:
        pairs = item.get("pairs") or []

        def _pair(p: object) -> dict | None:
            return pick(
                isinstance(p, dict)
                and bool(str(p.get("left") or "").strip())
                and bool(str(p.get("right") or "").strip()),
                lambda: {
                    "left": str(p.get("left") or "").strip(),
                    "right": str(p.get("right") or "").strip(),
                },
                _drop,
            )

        clean = list(filter(None, map(_pair, pairs)))
        return pick(
            len(clean) < 2,
            _drop,
            lambda: {**q, "pairs": clean[: fields.limit("pairs")]},
        )

    def _typed() -> dict | None:
        hit = first_match(_QTYPE_RULES, {"qtype": qtype})
        return apply(
            hit.action,
            {
                "single_mcq": _single_mcq,
                "multi": _multi,
                "truefalse": _truefalse,
                "text": _text,
                "matching": _matching,
                "drop": _drop,
            },
        )

    return pick(
        (not prompt) or (qtype not in QUESTION_TYPES) or (bool(allowed) and qtype not in allowed),
        _drop,
        _typed,
    )


def _finalize(raw_items: list, requested_types: list[str], cap: int) -> list[dict]:
    """Validate + normalize each question by its type; drop malformed ones."""
    allowed = set(requested_types) & set(QUESTION_TYPES)
    fields = plan_auxiliary_field_caps("quiz")
    shaped = [
        _shape_question(item, allowed, fields)
        for item in filter(lambda it: isinstance(it, dict), raw_items)
    ]
    return list(filter(None, shaped))[:cap]


def quiz_config_signature(types: list[str], count: int, difficulty: str) -> str:
    """Stable signature so a changed config triggers regeneration."""
    t = ",".join(sorted(set(types) & set(QUESTION_TYPES))) or "mcq"
    c = clamp_auxiliary_count("quiz", count)
    d = (difficulty or "mixed").strip().lower()
    return f"{t}|{c}|{d}"


def _cache_text(hit: object) -> str:
    return pick(isinstance(hit, str), lambda: str(hit).strip(), lambda: "")


async def generate_quiz(
    db: Session, document_id: uuid.UUID, *, types: list[str], count: int, difficulty: str
) -> list[dict]:
    """Generate a worksheet/quiz of the requested question types from the document."""
    chunk_texts = load_document_chunk_texts(db, document_id)
    body = "\n\n".join(chunk_texts)

    async def _empty() -> list[dict]:
        return []

    async def _generate() -> list[dict]:
        allowed = list(filter(lambda t: t in QUESTION_TYPES, types)) or ["mcq"]
        cap = clamp_auxiliary_count("quiz", count)
        diff = (difficulty or "mixed").strip().lower()

        from app.services.llm_registry import default_chat_model_id

        system = get_prompt(db, "quiz_system")
        type_lines = "\n".join(f"- {t}: {QUESTION_TYPES[t]}" for t in allowed)
        # Prefix-cache discipline: the document source is stable across the educator's
        # iterate-on-settings loop (change count/types/difficulty, regenerate), so it
        # leads and the per-run instructions ride last.
        user = (
            f"SOURCE:\n\n{truncate_to_tokens(body, SUMMARIZE_SINGLE_SHOT_MAX_TOKENS)}\n\n"
            f"Create exactly {cap} questions at {diff} difficulty, distributed across these "
            f"types (use only these):\n{type_lines}"
        )

        from app.services.chunk_map_cache import content_hash_key
        from app.services.generation_cache import get as cache_get, put as cache_put

        model_id = default_chat_model_id(db)
        quiz_key = content_hash_key("quiz_generate", system, user, str(model_id))
        hit = cache_get(db, kind="quiz_generate", cache_key=quiz_key)
        cached = _cache_text(hit)

        async def _hit() -> list[dict]:
            return _finalize(_parse_questions(hit), allowed, cap)  # type: ignore[arg-type]

        async def _call(mid: uuid.UUID | None) -> tuple[str, list[dict]]:
            raw = await complete_chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                db,
                log_tag="quiz_generate",
                model_id=mid,
            )
            return raw, _finalize(_parse_questions(raw), allowed, cap)

        async def _miss() -> list[dict]:
            raw, questions = await _call(model_id)
            route = evaluate_llm_route(
                has_primary=True, has_fallback=True, primary_failed=not questions
            )

            async def _keep() -> tuple[str, list[dict]]:
                return raw, questions

            async def _fallback() -> tuple[str, list[dict]]:
                return await _call(None)

            raw, questions = await apply(
                route.action,
                {"use_primary": _keep, "use_fallback": _fallback, "skip": _keep},
            )
            pick(
                bool(questions) and bool(raw),
                lambda: cache_put(db, kind="quiz_generate", cache_key=quiz_key, value=raw),
                lambda: None,
            )
            return questions

        return await apply(
            evaluate_presence(cached).action,
            {"ok": _hit, "empty": _miss, "missing": _miss},
        )

    return await apply(
        evaluate_presence(body.strip()).action,
        {"ok": _generate, "empty": _empty, "missing": _empty},
    )
