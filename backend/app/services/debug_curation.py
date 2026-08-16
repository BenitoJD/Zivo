"""Human-curated debug diagnostic scenarios — create/update without LLM generation.

Complements ``debug_generation`` (LLM cook from material). Same ``intel.assertion``
+ ``qb.debug_assertion_facets`` shape (``format: qb.debug.v1``).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import choose, pick
from app.repositories.intel import _concept_id, _source_id
from app.services.open_response import evaluate_debug_scenario_shape

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_DEBUG_TYPE_URI = "/vocab/assertion/question.debug"
_DEBUG_BANK_SOURCE = "debug-bank"

_VALID_SCENARIO_TYPES = frozenset(
    {
        "code_reading",
        "stack_trace",
        "log_analysis",
        "test_failure",
        "config_error",
        "concurrency",
        "api_contract",
        "debug_process",
    }
)
_VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard"})
_VALID_REVIEW = frozenset({"draft", "pending_review", "approved", "rejected"})


def _slugify(title: str) -> str:
    s = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return (s or "scenario")[:80]


def _normalize_tags(tags: list[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in tags or []:
        label = str(t).strip().lower()

        def _add() -> None:
            seen.add(label)
            out.append(label[:40])

        pick(bool(label) and label not in seen and len(out) < 12, _add, lambda: None)
    return out


def _normalize_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for raw in steps or []:
        def _add() -> None:
            key = str(raw.get("key") or f"step_{len(clean)}").strip()[:40]
            question = str(raw.get("question") or "").strip()
            options = list(
                filter(None, (str(o).strip() for o in (raw.get("options") or [])))
            )

            def _step() -> None:
                correct_index = int(raw.get("correct_index", 0))
                correct_index = pick(
                    correct_index < 0 or correct_index >= len(options),
                    lambda: 0,
                    lambda: correct_index,
                )
                clean.append(
                    {
                        "key": key,
                        "question": question[:2000],
                        "options": [o[:1000] for o in options[:6]],
                        "correct_index": correct_index,
                        "explanation": str(raw.get("explanation") or "").strip()[:2000],
                    }
                )

            pick(not question or len(options) < 2, lambda: None, _step)

        pick(isinstance(raw, dict), _add, lambda: None)
    return clean


def public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip answer keys and cook QA from learner-facing payload."""
    steps_out: list[dict[str, Any]] = []
    for step in payload.get("steps") or []:
        pick(
            isinstance(step, dict),
            lambda: steps_out.append(
                {
                    "key": step.get("key"),
                    "question": step.get("question", ""),
                    "options": step.get("options") or [],
                }
            ),
            lambda: None,
        )
    return {
        "format": payload.get("format", "qb.debug.v1"),
        "title": payload.get("title", ""),
        "scenario_type": payload.get("scenario_type", "code_reading"),
        "difficulty": payload.get("difficulty", "medium"),
        "tags": payload.get("tags") or [],
        "origin": payload.get("origin", "generated"),
        "case": payload.get("case") or {},
        "steps": steps_out,
        "step_count": len(steps_out),
    }


def editorial_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Admin/editor view — includes answers and cook QA."""
    return {
        **public_payload(payload),
        "steps": payload.get("steps") or [],
        "_cook_qa": payload.get("_cook_qa") or {},
        "cook_job_id": payload.get("cook_job_id"),
        "cook_provenance": payload.get("cook_provenance") or {},
    }


def build_full_payload(
    *,
    title: str,
    scenario_type: str,
    case: dict[str, Any],
    steps: list[dict[str, Any]],
    difficulty: str = "medium",
    tags: list[str] | None = None,
    origin: str = "curated",
    cook_job_id: str | None = None,
    artifact_id: str | None = None,
    page_number: int = 0,
    sequence: int = 0,
    cook_qa: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st = choose(scenario_type in _VALID_SCENARIO_TYPES, scenario_type, "code_reading")
    diff = choose(difficulty in _VALID_DIFFICULTIES, difficulty, "medium")
    steps_clean = _normalize_steps(steps)
    shape = evaluate_debug_scenario_shape(title=title, step_count=len(steps_clean))

    def _bad_shape() -> None:
        raise ValueError(
            pick(
                shape.reason == "no_steps",
                lambda: "At least one diagnostic step is required",
                lambda: "Title is required",
            )
        )

    pick(not shape.ok, _bad_shape, lambda: None)
    return {
        "format": "qb.debug.v1",
        "artifact_id": artifact_id,
        "page_number": page_number,
        "sequence": sequence,
        "title": title.strip()[:200],
        "scenario_type": st,
        "difficulty": diff,
        "tags": _normalize_tags(tags),
        "origin": origin,
        "cook_job_id": cook_job_id,
        "case": choose(isinstance(case, dict), case, {}),
        "steps": steps_clean,
        "cook_provenance": {},
        "_cook_qa": cook_qa or {},
    }


def upsert_curated_scenario(
    db: Session,
    *,
    title: str,
    scenario_type: str,
    case: dict[str, Any],
    steps: list[dict[str, Any]],
    difficulty: str = "medium",
    tags: list[str] | None = None,
    published: bool = False,
    review_status: str = "draft",
    owner_user_id: uuid.UUID | None = None,
    cook_job_id: uuid.UUID | None = None,
    assertion_id: uuid.UUID | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    """Create or update a curated debug scenario."""
    title_clean = title.strip()[:200]
    review = choose(review_status in _VALID_REVIEW, review_status, "draft")
    full_payload = build_full_payload(
        title=title_clean,
        scenario_type=scenario_type,
        case=case,
        steps=steps,
        difficulty=difficulty,
        tags=tags,
        origin="curated",
        cook_job_id=pick(bool(cook_job_id), lambda: str(cook_job_id), lambda: None),
    )
    fp_slug = slug or _slugify(title_clean)
    fingerprint = f"debug:curated:{fp_slug}"
    source_id = _source_id(db, _DEBUG_BANK_SOURCE)
    type_id = _concept_id(db, _DEBUG_TYPE_URI)

    def _insert() -> uuid.UUID:
        new_id = uuid.uuid4()
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
                "id": new_id,
                "type_id": type_id,
                "source_id": source_id,
                "uri": f"qb://assertion/{new_id}",
                "fp": fingerprint,
                "title": title_clean,
                "summary": str((case or {}).get("summary") or title_clean)[:500],
                "payload": json.dumps(full_payload),
            },
        )
        return new_id

    def _update() -> uuid.UUID:
        db.execute(
            text(
                """
                UPDATE intel.assertion
                SET title = :title,
                    summary = :summary,
                    payload = CAST(:payload AS jsonb),
                    status = 'active',
                    source_id = :source_id
                WHERE id = :id
                """
            ),
            {
                "id": assertion_id,
                "title": title_clean,
                "summary": str((case or {}).get("summary") or title_clean)[:500],
                "payload": json.dumps(full_payload),
                "source_id": source_id,
            },
        )
        return assertion_id

    assertion_id = pick(assertion_id is None, _insert, _update)

    db.execute(
        text(
            """
            INSERT INTO qb.debug_assertion_facets (
              assertion_id, cook_job_id, artifact_id, page_number, sequence,
              title, scenario_type, difficulty, step_count,
              published, origin, review_status, owner_user_id, tags
            )
            VALUES (
              :aid, :cook_job_id, NULL, 0, 0,
              :title, :scenario_type, :difficulty, :step_count,
              :published, 'curated', :review_status, :owner_user_id, :tags
            )
            ON CONFLICT (assertion_id) DO UPDATE SET
              title = EXCLUDED.title,
              scenario_type = EXCLUDED.scenario_type,
              difficulty = EXCLUDED.difficulty,
              step_count = EXCLUDED.step_count,
              published = EXCLUDED.published,
              review_status = EXCLUDED.review_status,
              tags = EXCLUDED.tags,
              origin = 'curated'
            """
        ),
        {
            "aid": assertion_id,
            "cook_job_id": cook_job_id,
            "title": title_clean,
            "scenario_type": full_payload["scenario_type"],
            "difficulty": full_payload["difficulty"],
            "step_count": len(full_payload["steps"]),
            "published": bool(published),
            "review_status": review,
            "owner_user_id": owner_user_id,
            "tags": full_payload["tags"],
        },
    )

    pub = public_payload(full_payload)
    pub["id"] = str(assertion_id)
    pub["published"] = bool(published)
    pub["review_status"] = review
    pub["origin"] = "curated"
    return pub


def set_published(db: Session, assertion_id: uuid.UUID, published: bool) -> None:
    row = db.execute(
        text(
            """
            UPDATE qb.debug_assertion_facets
            SET published = :published
            WHERE assertion_id = :aid
            RETURNING assertion_id
            """
        ),
        {"aid": assertion_id, "published": published},
    ).first()
    def _missing() -> None:
        raise LookupError("Not found")

    pick(not row, _missing, lambda: None)
    pick(
        published,
        lambda: db.execute(
            text(
                """
                UPDATE intel.assertion
                SET status = 'active', retracted_at = NULL, retraction_reason = NULL
                WHERE id = :id AND status = 'retracted'
                """
            ),
            {"id": assertion_id},
        ),
        lambda: None,
    )


def set_review_status(
    db: Session,
    assertion_id: uuid.UUID,
    review_status: str,
    *,
    published: bool | None = None,
) -> None:
    def _bad_review() -> None:
        raise ValueError("Invalid review_status")

    pick(review_status not in _VALID_REVIEW, _bad_review, lambda: None)
    params: dict[str, Any] = {"aid": assertion_id, "review_status": review_status}
    pub_clause = pick(
        published is not None,
        lambda: ", published = :published",
        lambda: "",
    )
    pick(
        published is not None,
        lambda: params.__setitem__("published", bool(published)),
        lambda: None,
    )
    row = db.execute(
        text(
            f"""
            UPDATE qb.debug_assertion_facets
            SET review_status = :review_status{pub_clause}
            WHERE assertion_id = :aid
            RETURNING assertion_id
            """
        ),
        params,
    ).first()

    def _missing() -> None:
        raise LookupError("Not found")

    pick(not row, _missing, lambda: None)
    pick(bool(published), lambda: set_published(db, assertion_id, True), lambda: None)


def soft_delete_curated(db: Session, assertion_id: uuid.UUID) -> None:
    db.execute(
        text(
            """
            UPDATE intel.assertion
            SET status = 'retracted', retracted_at = NOW(), retraction_reason = 'curator_delete'
            WHERE id = :id AND (payload->>'format') = 'qb.debug.v1'
            """
        ),
        {"id": assertion_id},
    )
    db.execute(
        text(
            """
            UPDATE qb.debug_assertion_facets
            SET published = false, review_status = 'rejected'
            WHERE assertion_id = :aid
            """
        ),
        {"aid": assertion_id},
    )


_STARTER_SCENARIOS: list[dict[str, Any]] = [
    {
        "slug": "off-by-one-binary-search",
        "title": "Off-by-one in binary search",
        "scenario_type": "code_reading",
        "difficulty": "medium",
        "tags": ["binary-search", "off-by-one"],
        "case": {
            "summary": "Search returns -1 for a value that exists in the array.",
            "artifacts": [
                {
                    "kind": "code",
                    "language": "python",
                    "content": (
                        "def search(a, target):\n"
                        "    lo, hi = 0, len(a)\n"
                        "    while lo < hi:\n"
                        "        mid = (lo + hi) // 2\n"
                        "        if a[mid] == target:\n"
                        "            return mid\n"
                        "        if a[mid] < target:\n"
                        "            lo = mid + 1\n"
                        "        else:\n"
                        "            hi = mid\n"
                        "    return -1"
                    ),
                },
                {"kind": "output", "label": "Actual", "content": "-1"},
                {"kind": "output", "label": "Expected", "content": "2"},
            ],
        },
        "steps": [
            {
                "key": "root_cause",
                "question": "What is the most likely root cause?",
                "options": [
                    "The array is not sorted before searching",
                    "The upper bound uses len(a) instead of len(a) - 1, skipping the last index",
                    "Integer division truncates mid incorrectly",
                    "The loop should use <= instead of <",
                ],
                "correct_index": 1,
                "explanation": "hi is initialized to len(a), so when lo == len(a) - 1 the last element is never checked.",
            },
            {
                "key": "fix_approach",
                "question": "What is the minimal fix?",
                "options": [
                    "Initialize hi to len(a) - 1 (or use half-open bounds consistently)",
                    "Wrap the return in try/except",
                    "Sort the array inside search()",
                    "Replace the while loop with recursion",
                ],
                "correct_index": 0,
                "explanation": "Adjust the upper bound so the last valid index is included in the search range.",
            },
            {
                "key": "verify",
                "question": "What would best confirm the fix?",
                "options": [
                    "Run search on an empty array only",
                    "Test target at index 0, last index, and middle of a sorted array",
                    "Increase array size to 1 million elements",
                    "Remove the early return when a[mid] == target",
                ],
                "correct_index": 1,
                "explanation": "Boundary indices are where off-by-one bugs usually hide.",
            },
        ],
    },
]


def seed_starter_bank(db: Session) -> dict[str, Any]:
    created = 0
    updated = 0
    for spec in _STARTER_SCENARIOS:
        before = db.execute(
            text("SELECT id FROM intel.assertion WHERE fingerprint = :fp"),
            {"fp": f"debug:curated:{spec['slug']}"},
        ).scalar()
        upsert_curated_scenario(
            db,
            title=spec["title"],
            scenario_type=spec["scenario_type"],
            case=spec["case"],
            steps=spec["steps"],
            difficulty=spec["difficulty"],
            tags=spec.get("tags") or [],
            published=True,
            review_status="approved",
            slug=spec["slug"],
        )
        updated += int(bool(before))
        created += int(not before)
    return {"created": created, "updated": updated, "total": len(_STARTER_SCENARIOS)}
