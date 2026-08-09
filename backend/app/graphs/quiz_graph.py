"""Quiz/worksheet generation (worker-only) — the Question Generator feature.

Turns a source into a structured set of questions of the educator's chosen types
(MCQ, multi-select MCQ, true/false, fill-in-the-blank, short answer, essay, matching)
at a chosen difficulty, each with an answer key — so it can be previewed and exported
as a worksheet. Mirrors notes_graph's map-reduce over the document's RAG chunks (no new
ingest); off the answer path in a background worker.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.services.chunks import load_document_chunk_texts
from app.services.llm_json import extract_json_array
from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt
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
# (prompt, options, answer_index, explanation) — so they validate, render,
# and export through the same single-best-answer code path.
SINGLE_ANSWER_MCQ_TYPES = ("mcq", "mcq_negative", "assertion_reason", "scenario", "cloze")
MAX_QUESTIONS = 40


def _parse_questions(raw: str) -> list[dict]:
    """Extract a JSON array of question objects (tolerant of fences/prose/truncation)."""
    return extract_json_array(raw)


def _norm_options(item: dict) -> list[str]:
    opts = item.get("options") or item.get("choices") or []
    return [str(o).strip() for o in opts if str(o).strip()][:8]


def _finalize(raw_items: list, requested_types: list[str], cap: int) -> list[dict]:
    """Validate + normalize each question by its type; drop malformed ones."""
    allowed = set(requested_types) & set(QUESTION_TYPES)
    out: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        qtype = str(item.get("type") or "").strip().lower()
        prompt = str(item.get("prompt") or item.get("question") or "").strip()
        if not prompt or qtype not in QUESTION_TYPES:
            continue
        if allowed and qtype not in allowed:
            continue
        expl = str(item.get("explanation") or "").strip()[:600]
        q: dict = {"type": qtype, "prompt": prompt[:600], "explanation": expl}

        if qtype in SINGLE_ANSWER_MCQ_TYPES or qtype == "multi":
            opts = _norm_options(item)
            if len(opts) < 2:
                continue
            q["options"] = opts
            if qtype in SINGLE_ANSWER_MCQ_TYPES:
                # All MCQ-variant styles (standard, negative/EXCEPT, assertion–reason,
                # scenario, cloze) are single-best-answer: same payload as plain mcq.
                idx = item.get("answer_index")
                if not isinstance(idx, int) or not (0 <= idx < len(opts)):
                    continue
                q["answer_index"] = idx
            else:
                idxs = item.get("answer_indices") or []
                idxs = [i for i in idxs if isinstance(i, int) and 0 <= i < len(opts)]
                if not idxs:
                    continue
                q["answer_indices"] = sorted(set(idxs))
        elif qtype == "truefalse":
            ans = item.get("answer")
            if isinstance(ans, str):
                ans = ans.strip().lower() in ("true", "t", "yes")
            if not isinstance(ans, bool):
                continue
            q["answer"] = ans
        elif qtype in ("fill_blank", "short", "essay"):
            ans = str(item.get("answer") or "").strip()
            if not ans:
                continue
            q["answer"] = ans[:1200]
        elif qtype == "matching":
            pairs = item.get("pairs") or []
            clean = [
                {"left": str(p.get("left") or "").strip(), "right": str(p.get("right") or "").strip()}
                for p in pairs
                if isinstance(p, dict) and str(p.get("left") or "").strip() and str(p.get("right") or "").strip()
            ]
            if len(clean) < 2:
                continue
            q["pairs"] = clean[:8]
        out.append(q)
        if len(out) >= cap:
            break
    return out


def quiz_config_signature(types: list[str], count: int, difficulty: str) -> str:
    """Stable signature so a changed config triggers regeneration."""
    t = ",".join(sorted(set(types) & set(QUESTION_TYPES))) or "mcq"
    c = max(1, min(int(count or 10), MAX_QUESTIONS))
    d = (difficulty or "mixed").strip().lower()
    return f"{t}|{c}|{d}"


async def generate_quiz(
    db: Session, document_id: uuid.UUID, *, types: list[str], count: int, difficulty: str
) -> list[dict]:
    """Generate a worksheet/quiz of the requested question types from the document."""
    chunk_texts = load_document_chunk_texts(db, document_id)
    body = "\n\n".join(chunk_texts)
    if not body.strip():
        return []

    allowed = [t for t in types if t in QUESTION_TYPES] or ["mcq"]
    cap = max(1, min(int(count or 10), MAX_QUESTIONS))
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
    if isinstance(hit, str) and hit.strip():
        return _finalize(_parse_questions(hit), allowed, cap)

    async def _call(model_id):
        raw = await complete_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            db,
            log_tag="quiz_generate",
            model_id=model_id,
        )
        return raw, _finalize(_parse_questions(raw), allowed, cap)

    raw, questions = await _call(model_id)
    if not questions:
        raw, questions = await _call(None)  # failover pool on empty/truncated
    if questions and raw:
        cache_put(db, kind="quiz_generate", cache_key=quiz_key, value=raw)
    return questions
