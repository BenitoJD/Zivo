"""Coding-question generation — LeetCode-style problems sourced from uploaded material.

Adapts the interview coding-round generator (``interview._generate_question``) into a
persisted, bank-grade pipeline. Two structural differences from the interview version:

1. **Generate → verify gate (non-negotiable).** LLM-produced test cases are wrong often
   enough that persisting them unchecked would fill the bank with un-gradeable problems.
   We run the LLM's own reference solution against its own tests via ``run_tests`` and
   only persist when the reference passes *every* hidden test. Fail → retry, then give
   up silently for that page. Silence beats a broken problem.
2. **Persistence as ``intel.assertion`` rows.** Mirrors MCQ persistence — same table,
   ``type_concept_id = /vocab/assertion/question.coding``, payload stamped
   ``format: qb.coding.v1``. Inherits the question graph, calibration, and tagging
   machinery for free. A ``qb.coding_assertion_facets`` row is written alongside for
   fast list queries (mirrors ``qb.mcq_assertion_facets``).

The reference solution is NOT shipped to the client — it stays in the payload for
editorial/debugging only, and the API never returns it.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.repositories.intel import _concept_id, _source_id
from app.services.code_execution import DEFAULT_LANGUAGE_ID, LANGUAGES, run_tests
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.open_response import (
    CODING_VERIFY_MAX_ATTEMPTS,
    evaluate_coding_bank_item,
    evaluate_coding_reference_verify,
    plan_coding_page_input_tokens,
    plan_coding_test_visibility,
    resolve_coding_language_id,
    should_record_coding_solve,
)
from app.services.question_budget import plan_coding_page_yield
from app.services.token_budget import truncate_to_tokens

logger = logging.getLogger(__name__)

# Retry budget lives in Open Response (orchestration re-exports).
_MAX_VERIFY_ATTEMPTS = CODING_VERIFY_MAX_ATTEMPTS

_GEN_SYSTEM = (
    "You are Zivo, an author of competitive programming practice problems in the style "
    "of LeetCode / HackerRank. You write self-contained problems that the learner solves "
    "by reading from stdin and writing to stdout — no function signatures, no classes, "
    "no test harness. Every problem you write must be grounded in the source material "
    "the learner uploaded (apply its concepts, do not copy its text). Reply with STRICT "
    "JSON only — no markdown, no preamble."
)

_GEN_SCHEMA = (
    '{"title":"short problem title",'
    '"statement":"full problem statement. MUST specify the exact stdin input format and '
    'the exact expected stdout output format, with a worked example.",'
    '"difficulty":"easy|medium|hard",'
    '"starter_code":"a runnable Python 3 stub that reads stdin and prints a placeholder '
    '(e.g. reads the input and prints 0)",'
    '"reference_solution":"a complete correct Python 3 solution that reads stdin and '
    'prints the right answer",'
    '"tests":[{"stdin":"exact input bytes","expected_output":"exact expected stdout bytes"}, ...],'
    '"concept":"the algorithm or data-structure concept this problem tests",'
    '"tags":["one","two"]}'
)

_GEN_RULES = (
    "Rules:\n"
    "- Self-contained stdin → stdout problem. No function signatures. No imports of "
    "external files. The whole program is one file.\n"
    "- 5 to 8 test cases. Each must have EXACT stdin and EXACT expected_output — no "
    "trailing prose, no comments. Cover the empty/trivial case, the general case, and "
    "at least one edge case.\n"
    "- reference_solution must be valid Python 3 (language_id 71) and must produce "
    "every expected_output from the matching stdin. You will be verified.\n"
    "- starter_code must be valid Python 3 that compiles and runs (it can print a "
    "placeholder) — the learner starts from it.\n"
    "- difficulty is your honest estimate of the problem's level.\n"
    "- Solvable in roughly 10–25 minutes. Never copy the source text verbatim; apply "
    "its concept to a fresh scenario."
)

# Metric concept URI for a coding-submit outcome — seeded in migration 022.
_CODING_PASSED_METRIC_URI = "/vocab/metric/coding.passed"


def generate_coding_for_page(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    page_text: str,
    count: int | None = None,
) -> list[dict[str, Any]]:
    """Generate + verify + persist up to ``count`` coding problems for one page.

    Returns the persisted public payloads (no hidden tests, no reference solution) so
    the caller (ETA job) can log what landed. Always commits what it persisted.
    """
    return pick(
        not page_text or not page_text.strip(),
        lambda: [],
        lambda: _generate_loop(db, document_id, page_number, page_text, count),
    )


def _generate_loop(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    page_text: str,
    count: int | None,
) -> list[dict[str, Any]]:
    excerpt = truncate_to_tokens(page_text, plan_coding_page_input_tokens())
    target = plan_coding_page_yield(count)
    persisted: list[dict[str, Any]] = []

    def go(n: int) -> list[dict[str, Any]]:
        return pick(n <= 0, lambda: persisted, lambda: step(n))

    def step(n: int) -> list[dict[str, Any]]:
        problem = _generate_and_verify(db, excerpt)
        return pick(not problem, lambda: persisted, lambda: add(n, problem))

    def add(n: int, problem: dict[str, Any]) -> list[dict[str, Any]]:
        sequence = _next_sequence(db, document_id, page_number)
        payload = _persist(db, document_id, page_number, sequence, problem)
        pick(bool(payload), lambda: persisted.append(payload), lambda: None)
        return go(n - 1)

    go(target)
    pick(
        bool(persisted),
        lambda: (
            db.commit(),
            logger.info(
                "coding generation: doc=%s page=%s persisted=%d",
                document_id,
                page_number,
                len(persisted),
            ),
        ),
        lambda: None,
    )
    return persisted


def _generate_and_verify(db: Session, page_excerpt: str) -> dict[str, Any] | None:
    """Generate one problem, then verify its reference solution against its tests.

    Retries up to ``_MAX_VERIFY_ATTEMPTS`` times. Returns the problem dict on success
    (reference passes all hidden tests), else None. The sandbox run is the source of
    truth — if the reference fails, the tests or the solution are wrong; either way
    the problem is unusable.
    """
    last: dict[str, Any] = {"p": None}

    def attempt() -> dict[str, Any] | None:
        problem = _llm_generate(db, page_excerpt)
        return pick(not problem, lambda: None, lambda: _verify_one(problem, last))

    result = next(filter(None, (attempt() for _ in range(_MAX_VERIFY_ATTEMPTS))), None)
    pick(
        result is None and last["p"] is not None,
        lambda: logger.info(
            "coding generation: discarding problem '%s' — reference failed verify gate",
            last["p"].get("title", "?"),
        ),
        lambda: None,
    )
    return result


def _verify_one(problem: dict[str, Any], last: dict[str, Any]) -> dict[str, Any] | None:
    last["p"] = problem
    language_id = resolve_coding_language_id(
        problem.get("language_id"), LANGUAGES, default=DEFAULT_LANGUAGE_ID
    )
    problem["language_id"] = language_id
    tests = problem.get("tests") or []
    reference = problem.get("reference_solution") or ""
    box = {"sandbox_error": False, "passed": 0, "total": 0}

    def _run_sandbox() -> None:
        try:
            sandbox = run_coro_in_worker(run_tests(reference, language_id, tests))
        except Exception:
            logger.debug("coding verify: sandbox error; retrying", exc_info=True)
            box["sandbox_error"] = True
            return
        box["sandbox_error"] = bool(sandbox.get("error"))
        box["passed"] = int(sandbox.get("passed") or 0)
        box["total"] = int(sandbox.get("total") or 0)

    pick(bool(tests) and bool(reference), _run_sandbox, lambda: None)
    gate = evaluate_coding_reference_verify(
        has_tests=bool(tests),
        has_reference=bool(reference),
        sandbox_error=box["sandbox_error"],
        passed=box["passed"],
        total=box["total"],
    )
    pick(not gate.persist, lambda: logger.debug("coding verify: %s; retrying", gate.reason), lambda: None)
    return pick(gate.persist, lambda: problem, lambda: None)


def _llm_generate(db: Session, page_excerpt: str) -> dict[str, Any] | None:
    """One LLM call → one parsed coding problem dict, or None on parse failure."""
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    draft_key = content_hash_key("coding_draft", page_excerpt)
    hit = cache_get(db, kind="coding_draft", cache_key=draft_key)
    return pick(
        isinstance(hit, dict) and bool(hit.get("statement")),
        lambda: dict(hit),
        lambda: _llm_generate_fresh(db, page_excerpt, draft_key, cache_put),
    )


def _llm_generate_fresh(
    db: Session, page_excerpt: str, draft_key: str, cache_put: Any
) -> dict[str, Any] | None:
    user = (
        f"SOURCE MATERIAL (a page the learner uploaded — apply its concept, do not copy):\n"
        f"{page_excerpt}\n\n"
        f"Write ONE competitive-programming problem that exercises a concept from the "
        f"source above.\n{_GEN_RULES}\nReturn JSON matching: {_GEN_SCHEMA}"
    )
    try:
        raw = run_coro_in_worker(
            acomplete_chat(
                [
                    {"role": "system", "content": _GEN_SYSTEM},
                    {"role": "user", "content": user},
                ],
                db,
                log_tag="coding_generate",
            )
        )
    except Exception:
        logger.debug("coding generation: LLM call failed", exc_info=True)
        return None
    problem = _parse_problem_json(raw)
    pick(
        bool(problem),
        lambda: cache_put(db, kind="coding_draft", cache_key=draft_key, value=problem),
        lambda: None,
    )
    return problem


def _parse_problem_json(raw: str) -> dict[str, Any] | None:
    """Extract the JSON object from an LLM response and validate its shape."""
    return pick(not raw or not raw.strip(), lambda: None, lambda: _parse_problem_block(raw.strip()))


def _parse_problem_block(block: str) -> dict[str, Any] | None:
    import re

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", block, re.DOTALL)
    extracted = pick(bool(fence), lambda: fence.group(1), lambda: _from_braces(block))
    return pick(extracted is None, lambda: None, lambda: _loads_problem(extracted))


def _from_braces(block: str) -> str | None:
    start = block.find("{")
    end = block.rfind("}")
    return pick(start < 0 or end <= start, lambda: None, lambda: block[start : end + 1])


def _loads_problem(block: str) -> dict[str, Any] | None:
    try:
        problem = json.loads(block)
    except json.JSONDecodeError:
        return None
    required = ("statement", "starter_code", "reference_solution", "tests")
    return pick(
        not isinstance(problem, dict)
        or not all(problem.get(k) for k in required)
        or not isinstance(problem.get("tests"), list),
        lambda: None,
        lambda: _with_default_language(problem),
    )


def _with_default_language(problem: dict[str, Any]) -> dict[str, Any]:
    problem.setdefault("language_id", DEFAULT_LANGUAGE_ID)
    return problem


def _next_sequence(db: Session, document_id: uuid.UUID, page_number: int) -> int:
    """Next 1-based sequence number for a coding assertion on this page."""
    row = db.execute(
        text(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
            FROM qb.coding_assertion_facets
            WHERE artifact_id = :aid AND page_number = :page
            """
        ),
        {"aid": document_id, "page": page_number},
    ).scalar()
    return int(row or 1)


def _split_tests(tests: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """First two cases are sample (shown to the learner); the rest are hidden."""
    plan = plan_coding_test_visibility(tests)
    return plan.sample, plan.hidden


def _persist(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    sequence: int,
    problem: dict[str, Any],
) -> dict[str, Any] | None:
    """Insert the assertion + facet row for one verified coding problem.

    Returns the *public* payload (sample tests only, no reference/hidden) for logging.
    """
    bank = evaluate_coding_bank_item(problem)
    return pick(not bank.ok, lambda: None, lambda: _persist_verified(
        db, document_id, page_number, sequence, problem, bank
    ))


def _persist_verified(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    sequence: int,
    problem: dict[str, Any],
    bank: Any,
) -> dict[str, Any] | None:
    sample_tests, hidden_tests = _split_tests(problem.get("tests") or [])
    return pick(
        not hidden_tests,
        lambda: None,
        lambda: _insert_coding_rows(
            db, document_id, page_number, sequence, problem, bank, sample_tests, hidden_tests
        ),
    )


def _insert_coding_rows(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    sequence: int,
    problem: dict[str, Any],
    bank: Any,
    sample_tests: list[dict[str, str]],
    hidden_tests: list[dict[str, str]],
) -> dict[str, Any] | None:
    from app.services.open_response import normalize_coding_difficulty

    assertion_id = uuid.uuid4()
    title = bank.resolved_title[:200]
    difficulty = normalize_coding_difficulty(problem.get("difficulty"))
    language_id = resolve_coding_language_id(
        problem.get("language_id"), LANGUAGES, default=DEFAULT_LANGUAGE_ID
    )
    statement = str(problem.get("statement") or "")

    full_payload = {
        "format": "qb.coding.v1",
        "artifact_id": str(document_id),
        "page_number": page_number,
        "sequence": sequence,
        "title": title,
        "statement": statement,
        "starter_code": problem.get("starter_code") or "",
        "language_id": language_id,
        "language_label": LANGUAGES.get(language_id, "Python (3.8)"),
        "difficulty": difficulty,
        "sample_tests": sample_tests,
        "hidden_tests": hidden_tests,
        "editor_solution": problem.get("reference_solution") or "",
        "concept": problem.get("concept") or "",
        "tags": problem.get("tags") or [],
        "origin": "generated",
    }

    tags = list(
        map(
            lambda t: str(t).strip().lower()[:40],
            filter(lambda t: str(t).strip(), problem.get("tags") or []),
        )
    )[:12]

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
                "type_id": _concept_id(db, "/vocab/assertion/question.coding"),
                "source_id": _source_id(db, "user-upload"),
                "uri": f"qb://assertion/{assertion_id}",
                "fp": f"coding:{document_id}:{page_number}:{sequence}",
                "title": title,
                "summary": statement[:500],
                "payload": json.dumps(full_payload),
            },
        )
        db.execute(
            text(
                """
                INSERT INTO qb.coding_assertion_facets (
                  assertion_id, artifact_id, page_number, sequence,
                  title, difficulty, language_id,
                  sample_test_count, hidden_test_count,
                  published, origin, tags
                )
                VALUES (
                  :aid, :artifact_id, :page, :seq,
                  :title, :difficulty, :language_id,
                  :sample_count, :hidden_count,
                  true, 'generated', :tags
                )
                ON CONFLICT (assertion_id) DO UPDATE SET
                  title = EXCLUDED.title,
                  difficulty = EXCLUDED.difficulty,
                  language_id = EXCLUDED.language_id,
                  sample_test_count = EXCLUDED.sample_test_count,
                  hidden_test_count = EXCLUDED.hidden_test_count,
                  tags = EXCLUDED.tags
                """
            ),
            {
                "aid": assertion_id,
                "artifact_id": document_id,
                "page": page_number,
                "seq": sequence,
                "title": title,
                "difficulty": difficulty,
                "language_id": language_id,
                "sample_count": len(sample_tests),
                "hidden_count": len(hidden_tests),
                "tags": tags,
            },
        )
    except Exception:
        logger.warning(
            "coding persist failed for doc=%s page=%s seq=%d",
            document_id, page_number, sequence, exc_info=True,
        )
        return None

    return {
        "assertion_id": str(assertion_id),
        "title": title,
        "difficulty": difficulty,
        "language_id": language_id,
        "concept": problem.get("concept") or "",
        "sample_test_count": len(sample_tests),
        "hidden_test_count": len(hidden_tests),
    }


def public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip hidden tests + reference solution from a coding assertion payload.

    The single chokepoint that guarantees the client never sees grading material.
    """
    return {
        "format": payload.get("format", "qb.coding.v1"),
        "title": payload.get("title", ""),
        "statement": payload.get("statement", ""),
        "starter_code": payload.get("starter_code", ""),
        "language_id": payload.get("language_id", DEFAULT_LANGUAGE_ID),
        "language_label": payload.get("language_label", "Python (3.8)"),
        "difficulty": payload.get("difficulty", "medium"),
        "sample_tests": payload.get("sample_tests", []),
        "test_count": len(payload.get("hidden_tests") or []),
        "concept": payload.get("concept", ""),
        "tags": payload.get("tags", []),
        "origin": payload.get("origin", "generated"),
    }


def record_coding_submit(
    db: Session,
    *,
    assertion_id: uuid.UUID,
    passed: bool,
    passed_count: int,
    total_count: int,
    language_id: int,
    subject_entity_id: uuid.UUID | None,
) -> None:
    """Record a coding submit as an immutable measurement.

    Coding is iterative (unlike MCQs): a learner submits many times while debugging.
    The measurement table's idempotency index ``measurement_answer_idempotent`` allows
    only ONE row per (subject, assertion, metric), which would freeze a learner's
    status at their first attempt forever. So we instead record only the *solved*
    transition: the first time a learner passes all hidden tests, we insert a row with
    value_numeric=1. Failed attempts don't write — absence of a row means "not yet
    solved". This matches LeetCode's mental model (you have the green check or you
    don't), keeps the idempotency contract intact for MCQs, and never needs to mutate
    an immutable row. The ``status`` API then distinguishes 'new' (no submit signal)
    from 'solved' (row present) — we lose 'attempted' as a persisted state, which is
    fine because the editor's own submitResult already shows the learner their latest
    outcome per-session.

    The per-case breakdown for the first SOLVE is captured in value_json for analytics.
    """
    pick(
        not should_record_coding_solve(
            has_subject=subject_entity_id is not None, passed=passed
        ),
        lambda: None,
        lambda: _insert_coding_measurement(
            db,
            assertion_id=assertion_id,
            passed_count=passed_count,
            total_count=total_count,
            language_id=language_id,
            subject_entity_id=subject_entity_id,
        ),
    )


def _insert_coding_measurement(
    db: Session,
    *,
    assertion_id: uuid.UUID,
    passed_count: int,
    total_count: int,
    language_id: int,
    subject_entity_id: uuid.UUID | None,
) -> None:
    value_json = {
        "passed_count": int(passed_count),
        "total_count": int(total_count),
        "language_id": int(language_id),
    }
    try:
        db.execute(
            text(
                """
                INSERT INTO intel.measurement (
                  metric_concept_id, subject_entity_id, source_assertion_id,
                  value_numeric, value_json, observed_at
                )
                VALUES (:metric_id, :entity_id, :assertion_id, 1,
                        CAST(:value_json AS jsonb), now())
                ON CONFLICT (subject_entity_id, source_assertion_id, metric_concept_id)
                  WHERE subject_entity_id IS NOT NULL AND source_assertion_id IS NOT NULL
                  DO NOTHING
                """
            ),
            {
                "metric_id": _concept_id(db, _CODING_PASSED_METRIC_URI),
                "entity_id": subject_entity_id,
                "assertion_id": assertion_id,
                "value_json": json.dumps(value_json),
            },
        )
    except Exception:
        # A measurement is the moat, not the response — never fail a submit over it.
        logger.warning("coding measurement insert failed for %s", assertion_id, exc_info=True)
