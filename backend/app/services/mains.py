"""Mains mode — Zivo-generated descriptive question + examiner-style grading.

The whole loop is decoupled onto the `io` worker lane (see app.eta.handlers.io):

    start  -> enqueue mains.generate -> run_mains_generation  -> status=awaiting_answer
    answer -> enqueue mains.grade    -> run_mains_grading     -> status=ready

Reading a handwritten answer photo is NOT a separate OCR engine — it's the vision
LLM (`complete_chat(require_vision=True)` on the image), so nothing runs in-process
and it scales with the LLM layer. Typed answers skip the vision call entirely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import choose, pick
from app.models import Document
from app.services.artifact_store import coerce_jsonb
from app.services.chunks import load_document_chunk_texts
from app.services.llm_json import extract_json_obj
from app.services.llm_registry import vision_chat_model_id
from app.services.llm_router import complete_chat
from app.services.open_response import (
    DEFAULT_MAINS_STRICTNESS,
    MAINS_AXES as AXES,
    MAINS_AXIS_ANCHORS as _AXIS_ANCHORS,
    MAINS_AXIS_MAX as AXIS_MAX,
    clamp_int as _clamp_int,
    plan_mains_attempt,
    shape_mains_result,
    unreadable_mains_result,
)
from app.services.token_budget import SUMMARIZE_SINGLE_SHOT_MAX_TOKENS, truncate_to_tokens
from app.services.vision import build_user_message, is_image_document

logger = logging.getLogger(__name__)

DEFAULT_STRICTNESS = DEFAULT_MAINS_STRICTNESS

_STRICTNESS_GUIDE = {
    "exam": (
        "Mark like a hard real examiner: no benefit of the doubt, reward only what is clearly and "
        "correctly present, keep comments terse. Most answers sit mid-band; above 70% is rare."
    ),
    "coaching": (
        "Mark like an honest coach: fair but firm, give partial credit for partially-formed points, "
        "and always name the single biggest fix."
    ),
    "gentle": (
        "Mark encouragingly: be generous with partial credit for genuine attempts and lead with what "
        "worked, while still noting what to improve."
    ),
}

_GEN_SYSTEM = (
    "You are a senior examiner for competitive descriptive exams (civil-services 'Mains' style). "
    "From the source material you set ONE high-quality descriptive question that tests understanding "
    "over recall, plus a hidden marking scheme. Reply with STRICT JSON only, no markdown, no preamble."
)
_GRADE_SYSTEM = (
    "You are an experienced examiner grading a descriptive exam answer with an ANALYTIC rubric, "
    "reference-guided against a hidden marking scheme. Follow these principles strictly:\n"
    "- EVIDENCE BEFORE VERDICT: award a scheme point ONLY if the answer genuinely USES it in an "
    "argument (not merely names the keyword); you must be able to quote the phrase that earns it.\n"
    "- SUBSTANCE, NOT LENGTH: a longer answer is not better; padding, repetition, and vague "
    "generalities earn nothing. Score density of correct, relevant points.\n"
    "- ACCURACY CAP: factual errors or fabricated content lower the mark below an honest partial.\n"
    "- DIRECTIVE GATE: if the answer ignores the question's directive, the directive axis cannot "
    "exceed 1 no matter how rich the content.\n"
    "- The mark must reflect the covered scheme points plus answer quality — never a number picked "
    "from nowhere. You judge the SAME facts regardless of severity; only the generosity of partial "
    "credit and the tone change with the grading severity.\n"
    "Reply with STRICT JSON only, no markdown, no preamble."
)


# ---------------------------------------------------------------------------
# side table (qb.document_mains) — raw SQL, resume-style
# ---------------------------------------------------------------------------

def _load_row(db: Session, document_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            "SELECT question, scheme, answer, result, config, status, error "
            "FROM qb.document_mains WHERE document_id = :id"
        ),
        {"id": document_id},
    ).mappings().first()
    return pick(
        not row,
        lambda: None,
        lambda: {
            "question": row["question"] or "",
            "scheme": coerce_jsonb(row["scheme"]) or {},
            "answer": row["answer"] or "",
            "result": coerce_jsonb(row["result"]) or {},
            "config": coerce_jsonb(row["config"]) or {},
            "status": row["status"],
            "error": row["error"],
        },
    )


def _save(
    db: Session,
    document_id: uuid.UUID,
    *,
    question: str,
    scheme: dict,
    answer: str,
    result: dict,
    config: dict,
    status: str,
    error: str | None = None,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_mains
                (document_id, question, scheme, answer, result, config, status, error, updated_at)
            VALUES (:id, :question, CAST(:scheme AS jsonb), :answer, CAST(:result AS jsonb),
                    CAST(:config AS jsonb), :status, :error, now())
            ON CONFLICT (document_id) DO UPDATE SET
                question = EXCLUDED.question, scheme = EXCLUDED.scheme, answer = EXCLUDED.answer,
                result = EXCLUDED.result, config = EXCLUDED.config, status = EXCLUDED.status,
                error = EXCLUDED.error, updated_at = now()
            """
        ),
        {
            "id": document_id,
            "question": question,
            "scheme": json.dumps(scheme or {}),
            "answer": answer,
            "result": json.dumps(result or {}),
            "config": json.dumps(config or {}),
            "status": status,
            "error": error,
        },
    )
    db.commit()


def public_state(row: dict[str, Any] | None) -> dict[str, Any]:
    """Client-facing state. Never exposes the raw marking scheme — the reveal is
    `result.scheme_hits`, only present once status == 'ready'."""
    def _missing() -> dict[str, Any]:
        return {
            "status": "missing",
            "question": "",
            "directive": "",
            "marks_max": 10,
            "strictness": DEFAULT_STRICTNESS,
            "input_kind": None,
            "answer": "",
            "result": None,
            "error": None,
        }

    def _present() -> dict[str, Any]:
        scheme = row["scheme"] or {}
        config = row["config"] or {}
        ready = row["status"] == "ready"
        return {
            "status": row["status"],
            "question": row["question"],
            "directive": scheme.get("directive", ""),
            "marks_max": int(scheme.get("marks_max") or config.get("marks_max") or 10),
            "strictness": config.get("strictness", DEFAULT_STRICTNESS),
            "input_kind": config.get("input_kind"),
            "answer": pick(ready, lambda: row["answer"], lambda: ""),
            "result": pick(ready, lambda: row["result"] or None, lambda: None),
            "error": row["error"],
        }

    return pick(not row, _missing, _present)


# ---------------------------------------------------------------------------
# API-facing entry points (sync; enqueue io jobs)
# ---------------------------------------------------------------------------

def load_mains(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    return public_state(_load_row(db, document_id))


def start_mains(db: Session, document_id: uuid.UUID, *, strictness: str, marks_max: int) -> dict[str, Any]:
    """Begin a fresh attempt: overwrite the row and enqueue question generation."""
    plan = plan_mains_attempt(strictness=strictness, marks_max=marks_max)
    strictness = plan.strictness
    marks_max = plan.marks_max
    _save(
        db,
        document_id,
        question="",
        scheme={"marks_max": marks_max},
        answer="",
        result={},
        config={"strictness": strictness, "marks_max": marks_max},
        status="generating",
        error=None,
    )
    from app.services.jobs import enqueue_mains_generate

    enqueue_mains_generate(db, document_id)
    return public_state(_load_row(db, document_id))


def submit_mains_answer(
    db: Session,
    document_id: uuid.UUID,
    *,
    text_answer: str = "",
    image_document_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Store the answer (typed text and/or an uploaded photo doc) and enqueue grading."""
    row = _load_row(db, document_id)
    def _not_ready() -> None:
        raise ValueError("No question is ready to answer yet.")

    pick(not row or row["status"] not in ("awaiting_answer", "ready", "grading"), _not_ready, lambda: None)
    answer = (text_answer or "").strip()

    def _no_answer() -> None:
        raise ValueError("Write an answer or upload a photo first.")

    pick(not answer and image_document_id is None, _no_answer, lambda: None)
    config = dict(row["config"] or {})
    config["input_kind"] = choose(image_document_id is not None, "handwritten", "typed")
    config["answer_image_id"] = pick(
        image_document_id is not None,
        lambda: str(image_document_id),
        lambda: None,
    )
    _save(
        db,
        document_id,
        question=row["question"],
        scheme=row["scheme"],
        answer=answer,
        result={},
        config=config,
        status="grading",
        error=None,
    )
    from app.services.jobs import enqueue_mains_grade

    enqueue_mains_grade(db, document_id)
    return public_state(_load_row(db, document_id))


# ---------------------------------------------------------------------------
# worker entry points (sync wrappers; asyncio.run the LLM work)
# ---------------------------------------------------------------------------

def run_mains_generation(db: Session, document_id: uuid.UUID) -> None:
    row = _load_row(db, document_id)

    def _run() -> None:
        marks_max = int((row["config"] or {}).get("marks_max") or 10)
        try:
            gen = asyncio.run(_generate(db, document_id, marks_max=marks_max))
            scheme = {
                "directive": gen["directive"],
                "marks_max": gen["marks_max"],
                "model_points": gen["model_points"],
            }
            _save(
                db,
                document_id,
                question=gen["question"],
                scheme=scheme,
                answer="",
                result={},
                config=row["config"],
                status="awaiting_answer",
                error=None,
            )
        except Exception:
            logger.exception("mains generation failed for %s", document_id)
            _save(
                db,
                document_id,
                question="",
                scheme=row["scheme"],
                answer="",
                result={},
                config=row["config"],
                status="failed",
                error="generation_failed",
            )

    pick(not row, lambda: None, _run)


def run_mains_grading(db: Session, document_id: uuid.UUID) -> None:
    row = _load_row(db, document_id)

    def _run() -> None:
        config = row["config"] or {}
        scheme = row["scheme"] or {}
        try:
            answer = row["answer"] or ""
            image_id = config.get("answer_image_id")
            answer = pick(
                bool(image_id) and not answer.strip(),
                lambda: asyncio.run(_ocr_image(db, uuid.UUID(str(image_id)))),
                lambda: answer,
            )
            result = pick(
                not answer.strip(),
                lambda: _unreadable_result(scheme),
                lambda: asyncio.run(
                    _grade(
                        db,
                        document_id,
                        question=row["question"],
                        scheme=scheme,
                        answer=answer,
                        strictness=config.get("strictness", DEFAULT_STRICTNESS),
                    )
                ),
            )
            _save(
                db,
                document_id,
                question=row["question"],
                scheme=scheme,
                answer=answer,
                result=result,
                config=config,
                status="ready",
                error=None,
            )
        except Exception:
            logger.exception("mains grading failed for %s", document_id)
            _save(
                db,
                document_id,
                question=row["question"],
                scheme=scheme,
                answer=row["answer"] or "",
                result={},
                config=config,
                status="awaiting_answer",
                error="grading_failed",
            )

    pick(not row, lambda: None, _run)


# ---------------------------------------------------------------------------
# LLM steps
# ---------------------------------------------------------------------------

async def _generate(db: Session, document_id: uuid.UUID, *, marks_max: int) -> dict[str, Any]:
    chunks = load_document_chunk_texts(db, document_id)
    body = "\n\n".join(chunks).strip()
    source = pick(
        bool(body),
        lambda: truncate_to_tokens(body, SUMMARIZE_SINGLE_SHOT_MAX_TOKENS),
        lambda: "",
    )
    words = str(plan_mains_attempt(strictness=DEFAULT_STRICTNESS, marks_max=marks_max).word_target)
    user = (
        f"SOURCE MATERIAL:\n{source or '(no extracted text — infer a sensible topic from the document)'}\n\n"
        f"Set ONE descriptive question worth {marks_max} marks (a good answer is about {words} words). "
        "Pick a directive word that shapes the demand (Discuss / Critically examine / Analyse / "
        "Evaluate / Comment / Elucidate / To what extent). Then write a HIDDEN marking scheme: the key "
        f"points a full-marks answer must cover, each with the marks it carries (summing to about {marks_max}).\n"
        'Return JSON: {"question":"...","directive":"...","marks_max":'
        + str(marks_max)
        + ',"model_points":[{"point":"...","marks":n}]}'
    )
    raw = await complete_chat(
        [{"role": "system", "content": _GEN_SYSTEM}, {"role": "user", "content": user}],
        db,
        log_tag="mains_gen",
        document_id=document_id,
    )
    data = extract_json_obj(raw)
    question = (data.get("question") or "").strip()
    def _no_question() -> None:
        raise ValueError("no question generated")

    pick(not question, _no_question, lambda: None)
    mm = int(data.get("marks_max") or marks_max) or marks_max
    directive = (data.get("directive") or "Discuss").strip()
    points: list[dict[str, Any]] = []
    for p in data.get("model_points") or []:
        pick(
            isinstance(p, dict) and bool((p.get("point") or "").strip()),
            lambda: points.append({"point": str(p["point"]).strip(), "marks": _clamp_int(p.get("marks"), 0, mm)}),
            lambda: None,
        )
    return {"question": question, "directive": directive, "marks_max": mm, "model_points": points}


async def _ocr_image(db: Session, image_document_id: uuid.UUID) -> str:
    doc = db.get(Document, image_document_id)
    async def _empty() -> str:
        return ""

    async def _ocr() -> str:
        from app.services.chunk_map_cache import content_hash_key
        from app.services.generation_cache import get as cache_get, put as cache_put

        ocr_key = content_hash_key("mains_ocr", doc.storage_key or str(doc.id))
        hit = cache_get(db, kind="mains_ocr", cache_key=ocr_key)

        async def _cached() -> str:
            return hit

        async def _call() -> str:
            content = build_user_message(
                "Transcribe this exam answer sheet to plain text, verbatim. Preserve paragraph and line "
                "breaks. Do NOT correct spelling/grammar, summarise, or add anything — output only the "
                "transcription of what is written.",
                doc,
                include_image=True,
                db=db,
            )

            async def _not_image() -> str:
                return ""

            async def _complete() -> str:
                raw = await complete_chat(
                    [
                        {"role": "system", "content": "You are a precise OCR engine for handwritten and typed exam answers."},
                        {"role": "user", "content": content},
                    ],
                    db,
                    model_id=vision_chat_model_id(db),
                    require_vision=True,
                    log_tag="mains_ocr",
                    document_id=doc.id,
                    strip_output=False,
                )
                out = (raw or "").strip()
                pick(bool(out), lambda: cache_put(db, kind="mains_ocr", cache_key=ocr_key, value=out), lambda: None)
                return out

            return await pick(isinstance(content, str), _not_image, _complete)

        return await pick(isinstance(hit, str) and bool(hit.strip()), _cached, _call)

    return await pick(not doc or not is_image_document(doc), _empty, _ocr)


async def _grade(
    db: Session,
    document_id: uuid.UUID,
    *,
    question: str,
    scheme: dict,
    answer: str,
    strictness: str,
) -> dict[str, Any]:
    marks_max = int(scheme.get("marks_max") or 10)
    directive = scheme.get("directive", "")
    points = list(
        filter(lambda p: str(p.get("point", "")).strip(), scheme.get("model_points") or [])
    )
    pts_txt = (
        "\n".join(f"{i + 1}. ({int(p.get('marks') or 0)}m) {p.get('point', '')}" for i, p in enumerate(points))
        or "(use your judgement)"
    )
    guide = _STRICTNESS_GUIDE.get(strictness, _STRICTNESS_GUIDE[DEFAULT_STRICTNESS])
    anchors_txt = "\n".join(f"- {key} ({label}): {_AXIS_ANCHORS[key]}" for key, label in AXES)
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    grade_key = content_hash_key(
        "mains_grade",
        question,
        truncate_to_tokens(answer, SUMMARIZE_SINGLE_SHOT_MAX_TOKENS),
        strictness,
        marks_max,
        json.dumps(points, sort_keys=True, default=str),
    )
    hit = cache_get(db, kind="mains_grade", cache_key=grade_key)

    async def _cached() -> dict[str, Any]:
        return hit

    async def _compute() -> dict[str, Any]:
        user = (
            f"GRADING SEVERITY: {guide}\n\n"
            f"QUESTION (directive '{directive}', out of {marks_max} marks):\n{question}\n\n"
            f"HIDDEN MARKING SCHEME — award each point's marks ONLY if the answer genuinely USES it in an "
            f"argument (not just names it):\n{pts_txt}\n\n"
            f"AXES — score each 0..{AXIS_MAX} against its anchor:\n{anchors_txt}\n\n"
            f"CANDIDATE'S ANSWER:\n{truncate_to_tokens(answer, SUMMARIZE_SINGLE_SHOT_MAX_TOKENS)}\n\n"
            f"Grade it. First decide, per NUMBERED scheme point, whether it is genuinely covered (be ready "
            f"to quote the phrase) — report each by its number in scheme_hits. Then award an integer mark "
            f"out of {marks_max} that reflects the covered scheme "
            "points plus answer quality — do NOT invent a number unmoored from the scheme, never reward "
            "length or repetition, and let wrong/fabricated content lower it. Score each axis against its "
            "anchor (directive axis <= 1 if the directive is ignored). Give at most 3 'keep doing' and 3 "
            "'improve' bullets (specific and actionable), one terse examiner one-liner, and up to 4 "
            "highlights each quoting a SHORT phrase from the answer with a kind (strong/weak/error) and a "
            "brief note.\n"
            'Return JSON: {"marks":n,'
            '"axes":{"directive":n,"structure":n,"coverage":n,"substantiation":n,"presentation":n},'
            '"axis_notes":{"directive":"...","structure":"...","coverage":"...","substantiation":"...","presentation":"..."},'
            '"scheme_hits":[{"index":1,"hit":true}],"keep_doing":["..."],"improve":["..."],'
            '"examiner_note":"...","highlights":[{"quote":"...","kind":"strong","comment":"..."}]}'
        )
        raw = await complete_chat(
            [{"role": "system", "content": _GRADE_SYSTEM}, {"role": "user", "content": user}],
            db,
            log_tag="mains_grade",
            document_id=document_id,
        )
        parsed = extract_json_obj(raw)

        async def _noop() -> None:
            return None

        async def _repair() -> None:
            nonlocal parsed
            repair = await complete_chat(
                [
                    {"role": "system", "content": _GRADE_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid JSON. Return ONLY a valid JSON object "
                            f"matching the requested shape for this grading task.\n\nMalformed response:\n{raw}"
                        ),
                    },
                ],
                db,
                log_tag="mains_grade_repair",
                document_id=document_id,
            )
            parsed = extract_json_obj(repair)

        await pick("marks" not in parsed, _repair, _noop)

        def _unparseable() -> None:
            raise ValueError("unparseable grade output")

        pick("marks" not in parsed, _unparseable, lambda: None)
        shaped = shape_mains_result(parsed, scheme, strictness).result
        cache_put(db, kind="mains_grade", cache_key=grade_key, value=shaped)
        return shaped

    return await pick(isinstance(hit, dict) and bool(hit), _cached, _compute)


# ---------------------------------------------------------------------------
# shaping / clamping - Open Response Measurement Engine
# ---------------------------------------------------------------------------

def _shape_result(data: dict[str, Any], scheme: dict, strictness: str = DEFAULT_STRICTNESS) -> dict[str, Any]:
    return shape_mains_result(data, scheme, strictness).result


def _unreadable_result(scheme: dict) -> dict[str, Any]:
    return unreadable_mains_result(scheme).result


def _band(marks: int, marks_max: int, strictness: str = DEFAULT_STRICTNESS) -> str:
    from app.services.open_response import mains_band

    return mains_band(marks, marks_max, strictness)
