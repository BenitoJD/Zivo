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
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.services.llm_json import extract_json_obj
from app.services.llm_router import complete_chat
from app.services.token_budget import truncate_to_tokens

RESUME_MAX_TOKENS = 6000

# ---------------------------------------------------------------- deterministic ATS checks
# Each returns (passed, detail). These mirror the mechanical checks an ATS/recruiter applies
# before any human reads the content — the parts an LLM shouldn't be trusted to "judge".
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_ACTION_VERBS = {
    "led", "built", "designed", "developed", "created", "launched", "improved", "increased",
    "reduced", "managed", "shipped", "implemented", "optimized", "delivered", "owned",
    "drove", "architected", "automated", "scaled", "migrated", "spearheaded",
}
_SECTIONS = {
    "experience": ("experience", "work history", "employment"),
    "education": ("education",),
    "skills": ("skills", "technical skills", "technologies"),
}


def deterministic_checks(resume: str) -> list[dict[str, Any]]:
    low = resume.lower()
    words = resume.split()
    wc = len(words)
    bullets = sum(low.count(b) for b in ("•", "- ", "* ", "▪"))
    quantified = len(re.findall(r"\b\d+%?\b", resume))
    action_hits = sum(1 for w in _ACTION_VERBS if w in low)
    first_person = sum(low.count(p) for p in (" i ", " my ", " me "))

    checks = [
        ("Contact email", bool(_EMAIL.search(resume)), "A parseable email address is present." if _EMAIL.search(resume) else "No email found — ATS needs one to contact you."),
        ("Phone number", bool(_PHONE.search(resume)), "Phone number present." if _PHONE.search(resume) else "Add a phone number."),
        ("Experience section", any(s in low for s in _SECTIONS["experience"]), "Found a work-experience section." if any(s in low for s in _SECTIONS["experience"]) else "Add a clearly labelled Experience section."),
        ("Education section", any(s in low for s in _SECTIONS["education"]), "Found an education section." if any(s in low for s in _SECTIONS["education"]) else "Add an Education section."),
        ("Skills section", any(s in low for s in _SECTIONS["skills"]), "Found a skills section." if any(s in low for s in _SECTIONS["skills"]) else "Add a Skills section with relevant keywords."),
        ("Reasonable length", 250 <= wc <= 900, f"{wc} words — good length." if 250 <= wc <= 900 else f"{wc} words — aim for ~1 page (250-900 words)."),
        ("Bullet points", bullets >= 3, f"{bullets} bullet points." if bullets >= 3 else "Use bullet points for achievements, not paragraphs."),
        ("Quantified impact", quantified >= 3, f"{quantified} numbers/metrics found." if quantified >= 3 else "Quantify achievements (%, $, counts) — ATS and recruiters reward metrics."),
        ("Strong action verbs", action_hits >= 4, f"{action_hits} action verbs." if action_hits >= 4 else "Start bullets with action verbs (Led, Built, Improved…)."),
        ("Third-person voice", first_person == 0, "No first-person pronouns." if first_person == 0 else "Drop first-person pronouns (I, my) — resumes are written impersonally."),
    ]
    return [{"name": n, "pass": bool(p), "detail": d} for (n, p, d) in checks]


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
                {"role": "user", "content": f"RESUME:\n{truncate_to_tokens(resume, RESUME_MAX_TOKENS)}"},
            ],
            db, log_tag="resume_ats",
        )
        data = _parse_json_obj(raw)
        content_score = _clamp(data.get("content_score"), 50)
        strengths = [str(s) for s in (data.get("strengths") or [])][:6]
        improvements = [str(s) for s in (data.get("improvements") or [])][:8]
        structured = data.get("structured") if isinstance(data.get("structured"), dict) else {}
    except Exception:
        pass  # deterministic checks still give a usable score

    overall = round(0.5 * det_score + 0.5 * content_score)
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
    """Rewrite bullets ATS-friendly, optionally aligned to a job description (live, not cached)."""
    resume = _resume_text(db, document_id)
    if not resume.strip():
        return {"summary": "", "bullets": [], "missing_keywords": [], "notes": "No resume text found."}
    jd = (job_description or "").strip()
    user = f"RESUME:\n{truncate_to_tokens(resume, RESUME_MAX_TOKENS)}"
    if jd:
        user += f"\n\nTARGET JOB DESCRIPTION:\n{truncate_to_tokens(jd, 2000)}"
    try:
        raw = await complete_chat(
            [{"role": "system", "content": _OPTIMIZE_SYSTEM}, {"role": "user", "content": user}],
            db, log_tag="resume_optimize",
        )
        data = _parse_json_obj(raw)
        return {
            "summary": str(data.get("summary") or ""),
            "bullets": [
                {"original": str(b.get("original", "")), "improved": str(b.get("improved", ""))}
                for b in (data.get("bullets") or []) if isinstance(b, dict) and b.get("improved")
            ][:20],
            "missing_keywords": [str(k) for k in (data.get("missing_keywords") or [])][:20],
            "notes": str(data.get("notes") or ""),
        }
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
        .limit(60)
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
