"""Resume suite — ATS scoring, optimization, and structured extraction (jobbie-style).

Additive career-tools feature. The learner uploads their resume (a normal source); we:
  - score it for ATS-friendliness (deterministic checks + an LLM content review),
  - extract a structured resume (so the builder can pre-fill + export .docx),
  - optimize it against an optional target job description (live, JD-specific).

Reuses the ingested resume chunks (``DocumentChunk``) + the shared LLM primitive
(``complete_chat``). The ATS analysis + structured resume are cached in qb.document_resume;
the optimizer is computed live because it depends on the pasted job description.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.services.llm_json import extract_json_obj
from app.services.llm_router import complete_chat
from app.services.open_response import (
    evaluate_resume_deterministic_checks,
    plan_resume_analysis_caps,
    plan_resume_ats_score,
    plan_resume_chunk_limit,
    plan_resume_input_tokens,
    plan_resume_jd_input_tokens,
    plan_resume_optimize_caps,
)
from app.services.token_budget import truncate_to_tokens


def deterministic_checks(resume: str) -> list[dict[str, Any]]:
    """Mechanical ATS checks live in Open Response; this is the orchestration alias."""
    return evaluate_resume_deterministic_checks(resume)


# ---------------------------------------------------------------- LLM analysis + extraction
_ANALYZE_SYSTEM = (
    "You are an expert technical recruiter and ATS specialist. Analyse the candidate's resume "
    "and return STRICT JSON only (no markdown), matching exactly: "
    '{"content_score": <0-100 integer for content quality & ATS-friendliness>, '
    '"strengths": ["..."], "improvements": ["specific, actionable fix", "..."], '
    '"structured": {"name":"","title":"","email":"","phone":"","location":"","links":["..."],'
    '"summary":"","experience":[{"role":"","company":"","dates":"","bullets":["..."]}],'
    '"education":[{"degree":"","school":"","dates":""}],"skills":["..."]}}. '
    "Extract structured fields verbatim from the resume where present; leave unknown fields empty."
)

_OPTIMIZE_SYSTEM = (
    "You are an expert resume writer. Rewrite the candidate's bullet points to be impact-driven, "
    "concise, ATS-friendly, and (when a job description is given) aligned to it — without inventing "
    "facts. Return STRICT JSON only: "
    '{"summary":"an optional improved 1-2 line professional summary or empty", '
    '"bullets":[{"original":"","improved":""}], "missing_keywords":["keywords from the JD the '
    'resume lacks"], "notes":"1-2 sentences of overall advice"}.'
)


def load_resume(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    row = db.execute(
        text("SELECT analysis, status, error, review_requested FROM qb.document_resume WHERE document_id = :id"),
        {"id": document_id},
    ).mappings().first()
    if not row:
        return {"status": "missing", "analysis": {}, "review_requested": False, "error": None}
    analysis = row["analysis"]
    if isinstance(analysis, str):
        analysis = json.loads(analysis)
    return {
        "status": row["status"],
        "analysis": analysis or {},
        "review_requested": bool(row["review_requested"]),
        "error": row["error"],
    }


def _save(db: Session, document_id: uuid.UUID, *, analysis: dict, status: str, error: str | None = None) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_resume (document_id, analysis, status, error, updated_at)
            VALUES (:id, CAST(:a AS jsonb), :status, :error, now())
            ON CONFLICT (document_id) DO UPDATE SET
                analysis = EXCLUDED.analysis, status = EXCLUDED.status,
                error = EXCLUDED.error, updated_at = now()
            """
        ),
        {"id": document_id, "a": json.dumps(analysis), "status": status, "error": error},
    )
    db.commit()


async def ensure_ats(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return the cached ATS analysis, computing it once on first request."""
    state = load_resume(db, document_id)
    if state["status"] == "ready" and state["analysis"]:
        return state

    resume = _resume_text(db, document_id)
    if not resume.strip():
        _save(db, document_id, analysis={}, status="failed", error="empty_resume")
        return load_resume(db, document_id)

    checks = deterministic_checks(resume)
    det_score = round(100 * sum(c["pass"] for c in checks) / len(checks))

    content_score, strengths, improvements, structured = 50, [], [], {}
    try:
        raw = await complete_chat(
            [
                {"role": "system", "content": _ANALYZE_SYSTEM},
                {"role": "user", "content": f"RESUME:\n{truncate_to_tokens(resume, plan_resume_input_tokens())}"},
            ],
            db, log_tag="resume_ats",
        )
        data = _parse_json_obj(raw)
        content_score = _clamp(data.get("content_score"), 50)
        caps = plan_resume_analysis_caps()
        strengths = [str(s) for s in (data.get("strengths") or [])][: caps.strengths]
        improvements = [str(s) for s in (data.get("improvements") or [])][: caps.improvements]
        structured = data.get("structured") if isinstance(data.get("structured"), dict) else {}
    except Exception:
        pass  # deterministic checks still give a usable score

    overall = plan_resume_ats_score(det_score=det_score, content_score=content_score)
    analysis = {
        "score": overall,
        "det_score": det_score,
        "content_score": content_score,
        "checks": checks,
        "strengths": strengths,
        "improvements": improvements,
        "structured": structured,
    }
    _save(db, document_id, analysis=analysis, status="ready")
    return load_resume(db, document_id)


async def optimize(db: Session, document_id: uuid.UUID, job_description: str = "") -> dict[str, Any]:
    """Rewrite bullets ATS-friendly, optionally aligned to a job description."""
    resume = _resume_text(db, document_id)
    if not resume.strip():
        return {"summary": "", "bullets": [], "missing_keywords": [], "notes": "No resume text found."}
    jd = (job_description or "").strip()
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    opt_key = content_hash_key(
        "resume_opt",
        truncate_to_tokens(resume, plan_resume_input_tokens()),
        truncate_to_tokens(jd, plan_resume_jd_input_tokens()) if jd else "",
    )
    hit = cache_get(db, kind="resume_optimize", cache_key=opt_key)
    if isinstance(hit, dict) and ("bullets" in hit or "summary" in hit):
        return hit
    user = f"RESUME:\n{truncate_to_tokens(resume, plan_resume_input_tokens())}"
    if jd:
        user += f"\n\nTARGET JOB DESCRIPTION:\n{truncate_to_tokens(jd, plan_resume_jd_input_tokens())}"
    try:
        raw = await complete_chat(
            [{"role": "system", "content": _OPTIMIZE_SYSTEM}, {"role": "user", "content": user}],
            db, log_tag="resume_optimize",
        )
        data = _parse_json_obj(raw)
        opt_caps = plan_resume_optimize_caps()
        out = {
            "summary": str(data.get("summary") or ""),
            "bullets": [
                {"original": str(b.get("original", "")), "improved": str(b.get("improved", ""))}
                for b in (data.get("bullets") or []) if isinstance(b, dict) and b.get("improved")
            ][: opt_caps.bullets],
            "missing_keywords": [
                str(k) for k in (data.get("missing_keywords") or [])
            ][: opt_caps.missing_keywords],
            "notes": str(data.get("notes") or ""),
        }
        cache_put(db, kind="resume_optimize", cache_key=opt_key, value=out)
        return out
    except Exception as exc:
        return {"summary": "", "bullets": [], "missing_keywords": [], "notes": f"Optimization unavailable: {exc}"}


def request_review(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Flag the resume for expert (manager) review. The manager-side dashboard is a later phase;
    this captures the request so it isn't lost."""
    db.execute(
        text(
            """
            INSERT INTO qb.document_resume (document_id, review_requested, status, updated_at)
            VALUES (:id, true, COALESCE((SELECT status FROM qb.document_resume WHERE document_id = :id), 'pending'), now())
            ON CONFLICT (document_id) DO UPDATE SET review_requested = true, updated_at = now()
            """
        ),
        {"id": document_id},
    )
    db.commit()
    return load_resume(db, document_id)


# ---------------------------------------------------------------- helpers
def _resume_text(db: Session, document_id: uuid.UUID) -> str:
    doc = db.get(Document, document_id)
    if not doc:
        return ""
    rows = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.page_start.asc())
        .limit(plan_resume_chunk_limit())
        .all()
    )
    return "\n\n".join(r.text for r in rows if r.text)


def _clamp(v: Any, default: int) -> int:
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


# Tolerant LLM-JSON extraction lives in app.services.llm_json (shared with interview.py).
_parse_json_obj = extract_json_obj


if __name__ == "__main__":  # pragma: no cover
    # ponytail: pure self-check — deterministic checks + score math + JSON parse.
    good = (
        "Jane Doe\njane@example.com  +1 415 555 1234\n"
        "Experience\n- Led a team that improved latency by 40%\n- Built and shipped 3 services\n"
        "- Reduced costs by 25% and increased signups 2x\nEducation\nBS CS, MIT\nSkills\nPython, Go, SQL"
    )
    checks = deterministic_checks(good)
    passed = sum(c["pass"] for c in checks)
    assert passed >= 8, [c for c in checks if not c["pass"]]

    bad = "i am a hard working person and i want a job. my email is missing."
    assert sum(c["pass"] for c in deterministic_checks(bad)) <= 4

    assert _clamp(150, 0) == 100 and _clamp(-5, 0) == 0 and _clamp("x", 42) == 42
    assert _parse_json_obj('```json\n{"content_score":80}\n```')["content_score"] == 80
    print("resume self-check OK")
