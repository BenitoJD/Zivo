"""Coding teach-gap — after submit: mentor truth, one lesson, next at the edge.

Same lesson JSON shape as System Design: title / body / try_this.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.llm_json import extract_json_obj
from app.services.llm_router import complete_chat
from app.services.open_response import plan_coding_teach_output_caps

_TEACH_SYSTEM = """You are a sharp coding mentor. The learner just submitted a solution to a programming problem.
Be fair but exacting. Reply ONLY with JSON:
{
  "mentor_summary": "2-3 sentences — what the tests reveal and the real gap",
  "weak_concepts": ["1-3 short tags or concept labels, e.g. two-pointers, edge-cases"],
  "lesson": {
    "title": "short principle name",
    "body": "1 short paragraph teaching the missing idea",
    "try_this": "one concrete thing to try on the next problem"
  }
}
If they passed all tests, still name one refinement edge (complexity, clarity, or a related pattern).
Prefer honest teaching over praise.
"""


def _normalize_focus(tags: list[str], concept: str) -> list[str]:
    out: list[str] = []
    for t in tags:
        s = str(t).strip().lower()
        if s and s not in out:
            out.append(s)
    c = str(concept or "").strip().lower()
    if c and c not in out:
        out.append(c)
    return out[: plan_coding_teach_output_caps().focus_tags]


def heuristic_teach_gap(
    *,
    all_passed: bool,
    passed: int,
    total: int,
    tags: list[str],
    concept: str,
    first_fail: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compat wrapper — canonical seam is Open Response ``heuristic_coding_teach_gap``."""
    from app.services.open_response import heuristic_coding_teach_gap

    return dict(
        heuristic_coding_teach_gap(
            all_passed=all_passed,
            passed=passed,
            total=total,
            tags=tags,
            concept=concept,
            first_fail=first_fail,
        ).result
    )


def pick_next_coding_id(
    db: Session,
    *,
    exclude: uuid.UUID,
    weak_concepts: list[str],
    tags: list[str],
    concept: str,
    attempted_ids: list[str] | None = None,
) -> uuid.UUID | None:
    """Prefer published problems overlapping weak tags/concept (Practice Selection Engine)."""
    from app.services.practice_selection import (
        PracticeCandidate,
        pick_next,
        plan_coding_next_pool_limit,
    )

    focus = list(_normalize_focus(list(weak_concepts) + list(tags), concept))
    rows = db.execute(
        text(
            """
            SELECT f.assertion_id AS id, f.difficulty, f.tags,
                   COALESCE(a.payload->>'concept', '') AS concept
            FROM qb.coding_assertion_facets f
            JOIN intel.assertion a ON a.id = f.assertion_id
            WHERE f.published = true AND a.status = 'active'
              AND f.assertion_id <> :exclude
            ORDER BY f.title ASC
            LIMIT :lim
            """
        ),
        {"exclude": exclude, "lim": plan_coding_next_pool_limit()},
    ).mappings().all()
    cands: list[PracticeCandidate] = []
    for r in rows:
        keys = tuple(_normalize_focus(list(r["tags"] or []), str(r["concept"] or "")))
        cands.append(
            PracticeCandidate(
                id=str(r["id"]),
                concept_keys=keys,
                difficulty=str(r["difficulty"] or "") or None,
            )
        )
    pick = pick_next(
        cands,
        focus,
        exclude_id=str(exclude),
        attempted_ids=attempted_ids,
    )
    return uuid.UUID(pick.id) if pick.id else None


def _solved_coding_ids(db: Session, subject_entity_id: uuid.UUID | None) -> list[str]:
    if subject_entity_id is None:
        return []
    rows = db.execute(
        text(
            """
            SELECT DISTINCT source_assertion_id::text AS id
            FROM intel.measurement
            WHERE subject_entity_id = :entity
              AND source_assertion_id IS NOT NULL
              AND metric_concept_id = (
                SELECT id FROM intel.concept WHERE uri = '/vocab/metric/coding.passed'
              )
            """
        ),
        {"entity": subject_entity_id},
    ).mappings().all()
    return [str(r["id"]) for r in rows if r.get("id")]


async def teach_after_submit(
    db: Session,
    *,
    assertion_id: uuid.UUID,
    payload: dict[str, Any],
    source: str,
    all_passed: bool,
    passed: int,
    total: int,
    cases: list[dict[str, Any]],
    subject_entity_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """LLM teach-gap with heuristic fallback. Same lesson shape as System Design."""
    import hashlib

    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    caps = plan_coding_teach_output_caps()
    tags = list(payload.get("tags") or [])
    concept = str(payload.get("concept") or "")
    title = str(payload.get("title") or "Coding problem")
    statement = str(payload.get("statement") or "")[: caps.statement]
    first_fail = next((c for c in cases if not c.get("ok")), None)
    graded = heuristic_teach_gap(
        all_passed=all_passed,
        passed=passed,
        total=total,
        tags=tags,
        concept=concept,
        first_fail=first_fail if isinstance(first_fail, dict) else None,
    )
    fail_blob = ""
    if first_fail and isinstance(first_fail, dict):
        fail_blob = (
            f"First fail stdin={first_fail.get('stdin')!r} "
            f"expected={first_fail.get('expected')!r} "
            f"stdout={first_fail.get('stdout')!r} stderr={first_fail.get('stderr')!r}"
        )
    source_trim = str(source)[: caps.source]
    teach_key = content_hash_key(
        "coding_teach_gap",
        str(assertion_id),
        all_passed,
        passed,
        total,
        hashlib.sha256(source_trim.encode("utf-8", "ignore")).hexdigest()[:32],
        hashlib.sha256(fail_blob.encode("utf-8", "ignore")).hexdigest()[:16],
    )
    cached = cache_get(db, kind="coding_teach_gap", cache_key=teach_key)
    if isinstance(cached, dict) and cached.get("mentor_summary") and cached.get("lesson"):
        graded = {
            "mentor_summary": str(cached["mentor_summary"]),
            "weak_concepts": list(
                cached.get("weak_concepts") or graded["weak_concepts"]
            )[: caps.weak_concepts],
            "lesson": {
                "title": str(
                    (cached.get("lesson") or {}).get("title") or graded["lesson"]["title"]
                )[: caps.title],
                "body": str(
                    (cached.get("lesson") or {}).get("body") or graded["lesson"]["body"]
                )[: caps.body],
                "try_this": str(
                    (cached.get("lesson") or {}).get("try_this")
                    or graded["lesson"]["try_this"]
                )[: caps.try_this],
            },
        }
    else:
        try:
            user = (
                f"Problem: {title}\n"
                f"Concept: {concept}\nTags: {', '.join(tags)}\n"
                f"Passed {passed}/{total} all_passed={all_passed}\n"
                f"Statement (trim):\n{statement}\n\n"
                f"Source (trim):\n{source_trim}\n\n"
                f"{fail_blob}"
            )
            raw = await complete_chat(
                [
                    {"role": "system", "content": _TEACH_SYSTEM},
                    {"role": "user", "content": user},
                ],
                db,
                log_tag="coding_teach_gap",
            )
            data = extract_json_obj(raw) or {}
            lesson_in = data.get("lesson") if isinstance(data.get("lesson"), dict) else {}
            weak = [
                str(w).strip().lower()
                for w in (data.get("weak_concepts") or [])
                if str(w).strip()
            ][: caps.weak_concepts]
            if not weak:
                weak = graded["weak_concepts"]
            graded = {
                "mentor_summary": str(data.get("mentor_summary") or "").strip()
                or graded["mentor_summary"],
                "weak_concepts": weak,
                "lesson": {
                    "title": str(lesson_in.get("title") or graded["lesson"]["title"])[
                        : caps.title
                    ],
                    "body": str(lesson_in.get("body") or graded["lesson"]["body"])[
                        : caps.body
                    ],
                    "try_this": str(
                        lesson_in.get("try_this") or graded["lesson"]["try_this"]
                    )[: caps.try_this],
                },
            }
            cache_put(db, kind="coding_teach_gap", cache_key=teach_key, value=graded)
        except Exception:
            # Best-effort LLM teach — heuristic graded above stays the response.
            pass

    next_id = pick_next_coding_id(
        db,
        exclude=assertion_id,
        weak_concepts=graded["weak_concepts"],
        tags=tags,
        concept=concept,
        attempted_ids=_solved_coding_ids(db, subject_entity_id),
    )
    ref = str(payload.get("editor_solution") or "").strip()
    return {
        "mentor_summary": graded["mentor_summary"],
        "weak_concepts": graded["weak_concepts"],
        "lesson": graded["lesson"],
        "recommended_next_id": str(next_id) if next_id else None,
        "reference_solution": ref or None,
    }
