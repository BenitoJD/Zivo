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

from app.engine_runtime import choose, pick
from app.repositories.intel import _concept_id, _source_id
from app.services.code_execution import DEFAULT_LANGUAGE_ID, run_tests
from app.services.debug_cook import get_cook_job, set_cook_job_status
from app.services.debug_curation import build_full_payload, public_payload
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.open_response import (
    CODING_VERIFY_MAX_ATTEMPTS,
    evaluate_debug_scenario_qa,
    evaluate_debug_scenario_shape,
    plan_debug_cook_input_tokens,
    plan_debug_cook_yield,
)
from app.services.token_budget import truncate_to_tokens

logger = logging.getLogger(__name__)

_DEBUG_TYPE_URI = "/vocab/assertion/question.debug"
_MAX_VERIFY_ATTEMPTS = CODING_VERIFY_MAX_ATTEMPTS

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

    def _strip_fence() -> str:
        lines = list(filter(lambda ln: not ln.strip().startswith("```"), text_clean.split("\n")))
        return "\n".join(lines).strip()

    text_clean = pick(text_clean.startswith("```"), _strip_fence, lambda: text_clean)
    try:
        data = json.loads(text_clean)
        return pick(isinstance(data, dict), lambda: data, lambda: None)
    except json.JSONDecodeError:
        return None


def _internal_qa_passes(draft: dict[str, Any]) -> bool:
    qa = draft.get("cook_qa") or {}
    buggy = pick(
        isinstance(qa, dict),
        lambda: str(qa.get("buggy_code") or "").strip(),
        lambda: "",
    )
    fixed = pick(
        isinstance(qa, dict),
        lambda: str(qa.get("reference_fix_code") or "").strip(),
        lambda: "",
    )
    tests = pick(isinstance(qa, dict), lambda: qa.get("tests") or [], lambda: [])
    clean_tests = [
        {
            "stdin": str(t.get("stdin", "")),
            "expected_output": str(t.get("expected_output", "")),
        }
        for t in filter(lambda t: isinstance(t, dict), tests)
    ]

    def _skip_run() -> bool:
        return evaluate_debug_scenario_qa(
            has_buggy=bool(buggy),
            has_fixed=bool(fixed),
            has_tests=bool(clean_tests),
            buggy_fails=False,
            fixed_passes=False,
        ).persist

    def _run() -> bool:
        buggy_result = run_tests(buggy, clean_tests, language_id=DEFAULT_LANGUAGE_ID)
        fixed_result = run_tests(fixed, clean_tests, language_id=DEFAULT_LANGUAGE_ID)
        buggy_fails = any(not r.get("passed") for r in buggy_result.get("results") or [])
        fixed_passes = all(r.get("passed") for r in fixed_result.get("results") or [])
        return evaluate_debug_scenario_qa(
            has_buggy=True,
            has_fixed=True,
            has_tests=True,
            buggy_fails=buggy_fails,
            fixed_passes=fixed_passes,
        ).persist

    return pick(not buggy or not fixed or not clean_tests, _skip_run, _run)


def _draft_scenario(db: Session, material: str, brief: str, index: int) -> dict[str, Any] | None:
    context = truncate_to_tokens(material, plan_debug_cook_input_tokens())
    user_msg = (
        f"SOURCE MATERIAL:\n{context}\n\n"
        f"COOK BRIEF:\n{brief or 'Generate one diagnostic scenario.'}\n\n"
        f"Scenario index: {index + 1}\n\n"
        f"Output schema:\n{_GEN_SCHEMA}\n\n{_GEN_RULES}"
    )
    found: dict[str, Any] | None = None
    for attempt in range(_MAX_VERIFY_ATTEMPTS):
        def _try() -> None:
            nonlocal found
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

            def _accept() -> None:
                nonlocal found
                found = draft

            pick(bool(draft) and _internal_qa_passes(draft), _accept, lambda: None)
            pick(
                bool(draft) and found is None,
                lambda: logger.info("debug cook QA failed attempt %d", attempt + 1),
                lambda: None,
            )

        pick(found is None, _try, lambda: None)
    return found


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
    steps = draft.get("steps") or []
    shape = evaluate_debug_scenario_shape(
        title=title,
        step_count=pick(isinstance(steps, list), lambda: len(steps), lambda: 0),
    )

    def _none() -> None:
        return None

    def _persist() -> dict[str, Any] | None:
        case = draft.get("case") or {}
        try:
            full_payload = build_full_payload(
                title=title,
                scenario_type=str(draft.get("scenario_type") or "code_reading"),
                case=choose(isinstance(case, dict), case, {}),
                steps=choose(isinstance(steps, list), steps, []),
                difficulty=str(draft.get("difficulty") or "medium"),
                tags=choose(isinstance(draft.get("tags"), list), draft.get("tags"), []),
                origin=origin,
                cook_job_id=str(cook_job_id),
                artifact_id=pick(bool(artifact_id), lambda: str(artifact_id), lambda: None),
                page_number=page_number,
                sequence=sequence,
                cook_qa=choose(
                    isinstance(draft.get("cook_qa"), dict),
                    draft.get("cook_qa"),
                    {},
                ),
            )
        except ValueError:
            return None

        full_payload["cook_provenance"] = {
            "source_hash": source_hash,
            "cook_job_id": str(cook_job_id),
        }

        assertion_id = uuid.uuid4()
        source_slug = choose(bool(artifact_id), "user-upload", "debug-bank")
        fp = pick(
            bool(artifact_id),
            lambda: f"debug:{artifact_id}:{page_number}:{sequence}",
            lambda: f"debug:{cook_job_id}:{sequence}",
        )

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

    return pick(not shape.ok, _none, _persist)


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
    def _missing() -> dict[str, Any]:
        set_cook_job_status(db, cook_job_id, "failed", error="job_not_found")
        db.commit()
        return {"saved": 0}

    def _cook() -> dict[str, Any]:
        m = row._mapping
        material_full = str(m["material"] or "")
        brief = str(m["brief"] or "")
        count = plan_debug_cook_yield(m["scenario_count"])
        origin = str(m["origin"] or "generated")
        owner = m["owner_user_id"]
        source_hash = hashlib.sha256(material_full.encode()).hexdigest()[:16]

        saved = 0
        for i in range(count):
            draft = _draft_scenario(db, material_full, brief, i)

            def _persist() -> None:
                nonlocal saved
                result = _persist_scenario(
                    db,
                    draft=draft,
                    cook_job_id=cook_job_id,
                    owner_user_id=owner,
                    origin=origin,
                    sequence=i + 1,
                    source_hash=source_hash,
                )
                saved += int(bool(result))

            pick(not draft, lambda: None, _persist)

        pick(
            saved > 0,
            lambda: set_cook_job_status(db, cook_job_id, "done"),
            lambda: set_cook_job_status(db, cook_job_id, "failed", error="no_scenarios_generated"),
        )
        db.commit()
        return {"saved": saved, "cook_job_id": str(cook_job_id)}

    return pick(not row, _missing, _cook)


def generate_debug_for_page(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    page_text: str,
    count: int | None = None,
    owner_user_id: uuid.UUID | None = None,
) -> int:
    """Auto-cook from a document page — creates a cook job and runs inline."""
    from app.services.debug_cook import create_cook_job

    n = plan_debug_cook_yield(count)
    job = create_cook_job(
        db,
        owner_user_id=owner_user_id,
        material=page_text,
        brief=f"Generate {n} debug diagnostic scenarios from this technical page.",
        scenario_count=n,
        source_type="document_page",
        source_ref=str(document_id),
        origin="generated",
    )
    job_id = uuid.UUID(job["id"])
    db.commit()
    result = run_cook_job(db, job_id)
    pick(
        result.get("saved", 0) > 0,
        lambda: (
            db.execute(
                text(
                    """
                    UPDATE qb.debug_assertion_facets
                    SET artifact_id = :aid, page_number = :page
                    WHERE cook_job_id = :jid
                    """
                ),
                {"aid": document_id, "page": page_number, "jid": job_id},
            ),
            db.commit(),
        ),
        lambda: None,
    )
    return int(result.get("saved") or 0)
