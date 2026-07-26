"""LLM cook pipeline for debug diagnostic scenarios.

Produces ``qb.debug.v1`` assertions: case file + stepped diagnostic MCQs.
Internal sandbox QA optional for code-grounded scenarios (cook-time only).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.intel import _concept_id, _source_id
from app.services.code_execution import DEFAULT_LANGUAGE_ID, run_tests
from app.services.debug_cook import get_cook_job, set_cook_job_status
from app.services.debug_curation import build_full_payload, public_payload
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.token_budget import truncate_to_tokens

logger = logging.getLogger(__name__)

_DEBUG_TYPE_URI = "/vocab/assertion/question.debug"
_MAX_VERIFY_ATTEMPTS = 3
_MATERIAL_MAX_TOKENS = 6000

_GEN_SYSTEM = (
    "You are Zivo, an author of software-engineering diagnostic scenarios. "
    "Learners READ artifacts and answer multiple-choice questions about what is "
    "wrong and how to fix it — they never write code. Each scenario has a case "
    "file (symptoms + artifacts) and 2-4 MCQ steps: root cause, fix approach "
    "(plain-language descriptions, not code to type), and optionally verification. "
    "Reply with STRICT JSON only — no markdown, no preamble."
)

_GEN_SCHEMA = (
    '{"title":"short title",'
    '"scenario_type":"code_reading|stack_trace|log_analysis|test_failure|config_error|concurrency|api_contract|debug_process",'
    '"difficulty":"easy|medium|hard",'
    '"tags":["tag1","tag2"],'
    '"case":{"summary":"one sentence symptom","artifacts":[{"kind":"code|output|log|trace|config","language":"python","label":"","content":"..."}]},'
    '"steps":[{"key":"root_cause|fix_approach|verify","question":"...","options":["...","...","...","..."],"correct_index":0,"explanation":"..."}],'
    '"cook_qa":{"buggy_code":"","reference_fix_code":"","tests":[{"stdin":"","expected_output":""}]}}'
)

_GEN_RULES = (
    "Rules:\n"
    "- One bug, one concept per scenario.\n"
    "- Code artifacts: 8-15 lines max.\n"
    "- Each step: exactly 4 options unless fewer are genuinely plausible.\n"
    "- Distractors must each fix a DIFFERENT plausible bug.\n"
    "- fix_approach options are plain-language descriptions (e.g. 'Change hi to len(a)-1'), never 'write code'.\n"
    "- For code_reading/test_failure: include cook_qa with buggy_code, reference_fix_code, and 2-4 tests.\n"
    "- Ground scenarios in the source material; do not invent unrelated topics.\n"
    "- cognitive_level: analyze/evaluate, not syntax trivia."
)


def _parse_json(raw: str) -> dict[str, Any] | None:
    text_clean = (raw or "").strip()
    if text_clean.startswith("```"):
        lines = text_clean.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text_clean = "\n".join(lines).strip()
    try:
        data = json.loads(text_clean)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _internal_qa_passes(draft: dict[str, Any]) -> bool:
    qa = draft.get("cook_qa") or {}
    if not isinstance(qa, dict):
        return True
    buggy = str(qa.get("buggy_code") or "").strip()
    fixed = str(qa.get("reference_fix_code") or "").strip()
    tests = qa.get("tests") or []
    if not buggy or not fixed or not tests:
        return True
    clean_tests = [
        {
            "stdin": str(t.get("stdin", "")),
            "expected_output": str(t.get("expected_output", "")),
        }
        for t in tests
        if isinstance(t, dict)
    ]
    if not clean_tests:
        return True
    buggy_result = run_tests(buggy, clean_tests, language_id=DEFAULT_LANGUAGE_ID)
    fixed_result = run_tests(fixed, clean_tests, language_id=DEFAULT_LANGUAGE_ID)
    buggy_fails = any(not r.get("passed") for r in buggy_result.get("results") or [])
    fixed_passes = all(r.get("passed") for r in fixed_result.get("results") or [])
    return buggy_fails and fixed_passes


def _draft_scenario(db: Session, material: str, brief: str, index: int) -> dict[str, Any] | None:
    context = truncate_to_tokens(material, _MATERIAL_MAX_TOKENS)
    user_msg = (
        f"SOURCE MATERIAL:\n{context}\n\n"
        f"COOK BRIEF:\n{brief or 'Generate one diagnostic scenario.'}\n\n"
        f"Scenario index: {index + 1}\n\n"
        f"Output schema:\n{_GEN_SCHEMA}\n\n{_GEN_RULES}"
    )
    for attempt in range(_MAX_VERIFY_ATTEMPTS):
        raw = run_coro_in_worker(
            acomplete_chat(
                [
                    {"role": "system", "content": _GEN_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                db,
                log_tag="debug_cook",
            )
        )
        draft = _parse_json(str(raw or ""))
        if not draft:
            continue
        if _internal_qa_passes(draft):
            return draft
        logger.info("debug cook QA failed attempt %d", attempt + 1)
    return None


def _persist_scenario(
    db: Session,
    *,
    draft: dict[str, Any],
    cook_job_id: uuid.UUID,
    owner_user_id: uuid.UUID | None,
    origin: str,
    sequence: int,
    source_hash: str,
    artifact_id: uuid.UUID | None = None,
    page_number: int = 0,
) -> dict[str, Any] | None:
    title = str(draft.get("title") or "").strip()[:200]
    if len(title) < 3:
        return None
    steps = draft.get("steps") or []
    case = draft.get("case") or {}
    try:
        full_payload = build_full_payload(
            title=title,
            scenario_type=str(draft.get("scenario_type") or "code_reading"),
            case=case if isinstance(case, dict) else {},
            steps=steps if isinstance(steps, list) else [],
            difficulty=str(draft.get("difficulty") or "medium"),
            tags=draft.get("tags") if isinstance(draft.get("tags"), list) else [],
            origin=origin,
            cook_job_id=str(cook_job_id),
            artifact_id=str(artifact_id) if artifact_id else None,
            page_number=page_number,
            sequence=sequence,
            cook_qa=draft.get("cook_qa") if isinstance(draft.get("cook_qa"), dict) else {},
        )
    except ValueError:
        return None

    full_payload["cook_provenance"] = {
        "source_hash": source_hash,
        "cook_job_id": str(cook_job_id),
    }

    assertion_id = uuid.uuid4()
    source_slug = "user-upload" if artifact_id else "debug-bank"
    fp = f"debug:{cook_job_id}:{sequence}"
    if artifact_id:
        fp = f"debug:{artifact_id}:{page_number}:{sequence}"

    try:
        db.execute(
            text(
                """
                INSERT INTO intel.assertion (
                  id, type_concept_id, source_id, canonical_uri, fingerprint,
                  title, summary, payload, status
                )
                VALUES (
                  :id, :type_id, :source_id, :uri, :fp,
                  :title, :summary, CAST(:payload AS jsonb), 'active'
                )
                """
            ),
            {
                "id": assertion_id,
                "type_id": _concept_id(db, _DEBUG_TYPE_URI),
                "source_id": _source_id(db, source_slug),
                "uri": f"qb://assertion/{assertion_id}",
                "fp": fp,
                "title": title,
                "summary": str(case.get("summary") or title)[:500],
                "payload": json.dumps(full_payload),
            },
        )
        db.execute(
            text(
                """
                INSERT INTO qb.debug_assertion_facets (
                  assertion_id, cook_job_id, artifact_id, page_number, sequence,
                  title, scenario_type, difficulty, step_count,
                  published, origin, review_status, owner_user_id, tags
                )
                VALUES (
                  :aid, :cook_job_id, :artifact_id, :page, :seq,
                  :title, :scenario_type, :difficulty, :step_count,
                  false, :origin, 'draft', :owner_user_id, :tags
                )
                """
            ),
            {
                "aid": assertion_id,
                "cook_job_id": cook_job_id,
                "artifact_id": artifact_id,
                "page": page_number,
                "seq": sequence,
                "title": title,
                "scenario_type": full_payload["scenario_type"],
                "difficulty": full_payload["difficulty"],
                "step_count": len(full_payload["steps"]),
                "origin": origin,
                "owner_user_id": owner_user_id,
                "tags": full_payload["tags"],
            },
        )
    except Exception:
        logger.warning("debug persist failed job=%s seq=%d", cook_job_id, sequence, exc_info=True)
        return None

    pub = public_payload(full_payload)
    pub["id"] = str(assertion_id)
    return pub


def run_cook_job(db: Session, cook_job_id: uuid.UUID) -> dict[str, Any]:
    """Execute one cook job — called from ETA worker."""
    set_cook_job_status(db, cook_job_id, "cooking")
    db.commit()

    try:
        get_cook_job(db, cook_job_id)
    except LookupError:
        return {"saved": 0, "error": "job_not_found"}

    row = db.execute(
        text("SELECT material, brief, scenario_count, origin, owner_user_id FROM qb.debug_cook_job WHERE id = :id"),
        {"id": cook_job_id},
    ).first()
    if not row:
        set_cook_job_status(db, cook_job_id, "failed", error="job_not_found")
        db.commit()
        return {"saved": 0}
    m = row._mapping
    material_full = str(m["material"] or "")
    brief = str(m["brief"] or "")
    count = int(m["scenario_count"] or 3)
    origin = str(m["origin"] or "generated")
    owner = m["owner_user_id"]
    source_hash = hashlib.sha256(material_full.encode()).hexdigest()[:16]

    saved = 0
    for i in range(count):
        draft = _draft_scenario(db, material_full, brief, i)
        if not draft:
            continue
        result = _persist_scenario(
            db,
            draft=draft,
            cook_job_id=cook_job_id,
            owner_user_id=owner,
            origin=origin,
            sequence=i + 1,
            source_hash=source_hash,
        )
        if result:
            saved += 1

    if saved > 0:
        set_cook_job_status(db, cook_job_id, "done")
    else:
        set_cook_job_status(db, cook_job_id, "failed", error="no_scenarios_generated")
    db.commit()
    return {"saved": saved, "cook_job_id": str(cook_job_id)}


def generate_debug_for_page(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    page_text: str,
    count: int = 2,
    owner_user_id: uuid.UUID | None = None,
) -> int:
    """Auto-cook from a document page — creates a cook job and runs inline."""
    from app.services.debug_cook import create_cook_job

    job = create_cook_job(
        db,
        owner_user_id=owner_user_id,
        material=page_text,
        brief=f"Generate {count} debug diagnostic scenarios from this technical page.",
        scenario_count=count,
        source_type="document_page",
        source_ref=str(document_id),
        origin="generated",
    )
    job_id = uuid.UUID(job["id"])
    db.commit()
    result = run_cook_job(db, job_id)
    if result.get("saved", 0) > 0:
        db.execute(
            text(
                """
                UPDATE qb.debug_assertion_facets
                SET artifact_id = :aid, page_number = :page
                WHERE cook_job_id = :jid
                """
            ),
            {"aid": document_id, "page": page_number, "jid": job_id},
        )
        db.commit()
    return int(result.get("saved") or 0)
