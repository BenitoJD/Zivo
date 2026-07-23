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

from app.models import Document
from app.services.artifact_store import coerce_jsonb
from app.services.chunks import load_document_chunk_texts
from app.services.llm_json import extract_json_obj
from app.services.llm_registry import vision_chat_model_id
from app.services.llm_router import complete_chat
from app.services.token_budget import SUMMARIZE_SINGLE_SHOT_MAX_TOKENS, truncate_to_tokens
from app.services.vision import build_user_message, is_image_document

logger = logging.getLogger(__name__)

STRICTNESS = ("exam", "coaching", "gentle")
DEFAULT_STRICTNESS = "coaching"

# Fixed diagnostic axes (0..AXIS_MAX dot-meters). The overall `marks` is separate
# and out of the question's `marks_max` — axes describe the answer, they don't sum.
AXES: tuple[tuple[str, str], ...] = (
    ("directive", "Directive"),
    ("structure", "Structure"),
    ("coverage", "Coverage"),
    ("substantiation", "Substantiation"),
    ("presentation", "Presentation"),
)
AXIS_MAX = 5

# Behavioral anchors per axis — the grader scores against observable descriptors, not a
# vague 0-5 Likert (anchored analytic rubrics beat holistic scoring on reliability).
_AXIS_ANCHORS = {
    "directive": "Did the answer DO what the directive demanded (e.g. 'critically examine' needs a weighed judgement, not mere description)? 0 = ignores it, 2-3 = partly, 5 = fully meets the demand.",
    "structure": "Intro that frames (not restates the question), a logically ordered body, and a conclusion that adds a verdict/way-forward. 0 = formless, 5 = builds a clear argument.",
    "coverage": "How much of the question's FULL demand and its relevant dimensions are addressed. 0 = one-track/off-topic, 5 = every part + multiple relevant dimensions.",
    "substantiation": "Are claims backed with examples, data, reports, articles, or cases (not bare assertion)? 0 = unsupported, 5 = well-evidenced throughout.",
    "presentation": "Legibility, headings, crisp expression, near the word limit. Score expression only; do NOT heavily penalise a content-strong answer here.",
}

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
    if not row:
        return None
    return {
        "question": row["question"] or "",
        "scheme": coerce_jsonb(row["scheme"]) or {},
        "answer": row["answer"] or "",
        "result": coerce_jsonb(row["result"]) or {},
        "config": coerce_jsonb(row["config"]) or {},
        "status": row["status"],
        "error": row["error"],
    }


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
    if not row:
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
        "answer": row["answer"] if ready else "",
        "result": (row["result"] or None) if ready else None,
        "error": row["error"],
    }


# ---------------------------------------------------------------------------
# API-facing entry points (sync; enqueue io jobs)
# ---------------------------------------------------------------------------

def load_mains(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    return public_state(_load_row(db, document_id))


def start_mains(db: Session, document_id: uuid.UUID, *, strictness: str, marks_max: int) -> dict[str, Any]:
    """Begin a fresh attempt: overwrite the row and enqueue question generation."""
    strictness = strictness if strictness in STRICTNESS else DEFAULT_STRICTNESS
    marks_max = 15 if int(marks_max or 10) >= 13 else 10
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
    if not row or row["status"] not in ("awaiting_answer", "ready", "grading"):
        raise ValueError("No question is ready to answer yet.")
    answer = (text_answer or "").strip()
    if not answer and image_document_id is None:
        raise ValueError("Write an answer or upload a photo first.")
    config = dict(row["config"] or {})
    config["input_kind"] = "handwritten" if image_document_id is not None else "typed"
    config["answer_image_id"] = str(image_document_id) if image_document_id is not None else None
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
    if not row:
        return
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


def run_mains_grading(db: Session, document_id: uuid.UUID) -> None:
    row = _load_row(db, document_id)
    if not row:
        return
    config = row["config"] or {}
    scheme = row["scheme"] or {}
    try:
        answer = row["answer"] or ""
        image_id = config.get("answer_image_id")
        if image_id and not answer.strip():
            answer = asyncio.run(_ocr_image(db, uuid.UUID(str(image_id))))
        if not answer.strip():
            result = _unreadable_result(scheme)
        else:
            result = asyncio.run(
                _grade(
                    db,
                    document_id,
                    question=row["question"],
                    scheme=scheme,
                    answer=answer,
                    strictness=config.get("strictness", DEFAULT_STRICTNESS),
                )
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
        # The question + scheme are still valid — revert to awaiting_answer (not the
        # terminal "failed", which routes the UI to the regenerate-only setup screen) so
        # the user can just resubmit. "failed" is reserved for generation failure.
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


# ---------------------------------------------------------------------------
# LLM steps
# ---------------------------------------------------------------------------

async def _generate(db: Session, document_id: uuid.UUID, *, marks_max: int) -> dict[str, Any]:
    chunks = load_document_chunk_texts(db, document_id)
    body = "\n\n".join(chunks).strip()
    source = truncate_to_tokens(body, SUMMARIZE_SINGLE_SHOT_MAX_TOKENS) if body else ""
    words = "250" if marks_max >= 13 else "150"
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
    if not question:
        raise ValueError("no question generated")
    mm = int(data.get("marks_max") or marks_max) or marks_max
    directive = (data.get("directive") or "Discuss").strip()
    points: list[dict[str, Any]] = []
    for p in data.get("model_points") or []:
        if isinstance(p, dict) and (p.get("point") or "").strip():
            points.append({"point": str(p["point"]).strip(), "marks": _clamp_int(p.get("marks"), 0, mm)})
    return {"question": question, "directive": directive, "marks_max": mm, "model_points": points}


async def _ocr_image(db: Session, image_document_id: uuid.UUID) -> str:
    doc = db.get(Document, image_document_id)
    if not doc or not is_image_document(doc):
        return ""
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    ocr_key = content_hash_key("mains_ocr", doc.storage_key or str(doc.id))
    hit = cache_get(db, kind="mains_ocr", cache_key=ocr_key)
    if isinstance(hit, str) and hit.strip():
        return hit
    content = build_user_message(
        "Transcribe this exam answer sheet to plain text, verbatim. Preserve paragraph and line "
        "breaks. Do NOT correct spelling/grammar, summarise, or add anything — output only the "
        "transcription of what is written.",
        doc,
        include_image=True,
        db=db,
    )
    if isinstance(content, str):  # not actually an image doc → nothing to read
        return ""
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
        strip_output=False,  # transcription must be verbatim (keep the user's own dashes)
    )
    out = (raw or "").strip()
    if out:
        cache_put(db, kind="mains_ocr", cache_key=ocr_key, value=out)
    return out


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
    points = [p for p in (scheme.get("model_points") or []) if str(p.get("point", "")).strip()]
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
    if isinstance(hit, dict) and hit:
        return hit
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
    # extract_json_obj returns {} on ANY parse failure — don't shape that into a
    # confident-looking 0/10. A real grade always carries "marks"; else fail (the
    # caller reverts to awaiting_answer so the user can resubmit).
    if "marks" not in parsed:
        raise ValueError("unparseable grade output")
    shaped = _shape_result(parsed, scheme, strictness)
    cache_put(db, kind="mains_grade", cache_key=grade_key, value=shaped)
    return shaped


# ---------------------------------------------------------------------------
# shaping / clamping
# ---------------------------------------------------------------------------

def _shape_result(data: dict[str, Any], scheme: dict, strictness: str = DEFAULT_STRICTNESS) -> dict[str, Any]:
    marks_max = int(scheme.get("marks_max") or 10)
    marks = _clamp_int(data.get("marks"), 0, marks_max)
    axis_scores = data.get("axes") if isinstance(data.get("axes"), dict) else {}
    axis_notes = data.get("axis_notes") if isinstance(data.get("axis_notes"), dict) else {}
    axes = [
        {
            "key": key,
            "label": label,
            "score": _clamp_int(axis_scores.get(key), 0, AXIS_MAX),
            "max": AXIS_MAX,
            "comment": str(axis_notes.get(key, "") or "").strip(),
        }
        for key, label in AXES
    ]
    # Match scheme hits by the 1-based index the grader returns (see _grade prompt).
    # Exact-text matching failed whenever the model paraphrased a point → all "missed".
    hits_by_index = {
        int(h["index"]): bool(h.get("hit"))
        for h in (data.get("scheme_hits") or [])
        if isinstance(h, dict) and isinstance(h.get("index"), (int, float))
    }
    points = [p for p in (scheme.get("model_points") or []) if str(p.get("point", "")).strip()]
    scheme_hits = [
        {
            "point": str(p.get("point", "")).strip(),
            "marks": int(p.get("marks") or 0),
            "hit": hits_by_index.get(i + 1, False),
        }
        for i, p in enumerate(points)
    ]
    return {
        "marks": marks,
        "marks_max": marks_max,
        "band": _band(marks, marks_max, strictness),
        "axes": axes,
        "scheme_hits": scheme_hits,
        "keep_doing": _str_list(data.get("keep_doing"))[:3],
        "improve": _str_list(data.get("improve"))[:3],
        "examiner_note": str(data.get("examiner_note", "") or "").strip(),
        "highlights": _shape_highlights(data.get("highlights")),
    }


def _unreadable_result(scheme: dict) -> dict[str, Any]:
    marks_max = int(scheme.get("marks_max") or 10)
    return {
        "marks": 0,
        "marks_max": marks_max,
        "band": "Needs work",
        "axes": [{"key": k, "label": l, "score": 0, "max": AXIS_MAX, "comment": ""} for k, l in AXES],
        "scheme_hits": [
            {"point": str(p.get("point", "")).strip(), "marks": int(p.get("marks") or 0), "hit": False}
            for p in (scheme.get("model_points") or [])
            if str(p.get("point", "")).strip()
        ],
        "keep_doing": [],
        "improve": ["Make sure the answer is legible and in focus, or type it out, then resubmit."],
        "examiner_note": "We couldn't read any answer to grade.",
        "highlights": [],
    }


def _band(marks: int, marks_max: int, strictness: str = DEFAULT_STRICTNESS) -> str:
    pct = (marks / marks_max * 100) if marks_max else 0
    # Strictness-calibrated cutoffs: an "exam" 70% and a "gentle" 70% shouldn't wear the
    # same label. Exam sets a hard bar (top band rare, mirroring real exam marking).
    hi, mid, lo = {
        "exam": (75, 60, 42),
        "coaching": (70, 55, 40),
        "gentle": (62, 48, 33),
    }.get(strictness, (70, 55, 40))
    if pct >= hi:
        return "Excellent"
    if pct >= mid:
        return "Good"
    if pct >= lo:
        return "Average"
    return "Needs work"


def _clamp_int(value: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return lo


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v or "").strip()]


def _shape_highlights(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for h in value[:4]:
        if not isinstance(h, dict):
            continue
        quote = str(h.get("quote", "") or "").strip()
        if not quote:
            continue
        kind = str(h.get("kind", "") or "").strip().lower()
        if kind not in ("strong", "weak", "error"):
            kind = "weak"
        out.append({"quote": quote, "kind": kind, "comment": str(h.get("comment", "") or "").strip()})
    return out
