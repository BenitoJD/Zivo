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
    return out[:6]


def heuristic_teach_gap(
    *,
    all_passed: bool,
    passed: int,
    total: int,
    tags: list[str],
    concept: str,
    first_fail: dict[str, Any] | None,
) -> dict[str, Any]:
    """Deterministic mentor + lesson when the model is unavailable."""
    focus = _normalize_focus(tags, concept)
    weak = focus[:2] or ["edge-cases"]
    if all_passed:
        return {
            "mentor_summary": (
                "Tests are green. The next edge is applying the same pattern under a twist — "
                "constraints change, or the data structure choice gets costly."
            ),
            "weak_concepts": weak,
            "lesson": {
                "title": "Own the pattern, then stretch it",
                "body": (
                    "Passing tests means the happy path works. Solid mastery is recognizing when "
                    "the same idea needs a different cut of the input or a tighter bound."
                ),
                "try_this": "On the next problem, name the pattern in one sentence before coding.",
            },
        }
    fail_hint = ""
    if first_fail:
        stderr = str(first_fail.get("stderr") or "").strip()
        if stderr:
            fail_hint = " Runtime/compile noise showed up — fix that before chasing logic."
        elif first_fail.get("expected") is not None:
            fail_hint = " Your output diverged from the expected case — check boundaries and off-by-one."
    ratio = f"{passed}/{total}" if total else "0/0"
    return {
        "mentor_summary": (
            f"You cleared {ratio} hidden tests.{fail_hint} "
            "The gap is usually one missed invariant, not more code."
        ),
        "weak_concepts": weak,
        "lesson": {
            "title": "Read the failing case as a clue",
            "body": (
                "A single failing input usually points at a boundary you skipped: empty, one element, "
                "duplicates, or the last index. Restate the invariant the solution must keep, then fix that."
            ),
            "try_this": "Before re-submitting, write the invariant in one line above your loop.",
        },
    }


def pick_next_coding_id(
    db: Session,
    *,
    exclude: uuid.UUID,
    weak_concepts: list[str],
    tags: list[str],
    concept: str,
) -> uuid.UUID | None:
    """Prefer unpublished-excluded published problems overlapping weak tags/concept."""
    focus = set(_normalize_focus(list(weak_concepts) + list(tags), concept))
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
            LIMIT 80
            """
        ),
        {"exclude": exclude},
    ).mappings().all()
    best: tuple[int, uuid.UUID] | None = None
    for r in rows:
        keys = set(_normalize_focus(list(r["tags"] or []), str(r["concept"] or "")))
        overlap = len(focus & keys)
        score = overlap * 10 + {"easy": 1, "medium": 2, "hard": 3}.get(str(r["difficulty"] or ""), 0)
        aid = r["id"]
        if isinstance(aid, uuid.UUID):
            uid = aid
        else:
            uid = uuid.UUID(str(aid))
        if best is None or score > best[0]:
            best = (score, uid)
    return best[1] if best else None


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
) -> dict[str, Any]:
    """LLM teach-gap with heuristic fallback. Same lesson shape as System Design."""
    tags = list(payload.get("tags") or [])
    concept = str(payload.get("concept") or "")
    title = str(payload.get("title") or "Coding problem")
    statement = str(payload.get("statement") or "")[:1200]
    first_fail = next((c for c in cases if not c.get("ok")), None)
    graded = heuristic_teach_gap(
        all_passed=all_passed,
        passed=passed,
        total=total,
        tags=tags,
        concept=concept,
        first_fail=first_fail if isinstance(first_fail, dict) else None,
    )
    try:
        fail_blob = ""
        if first_fail and isinstance(first_fail, dict):
            fail_blob = (
                f"First fail stdin={first_fail.get('stdin')!r} "
                f"expected={first_fail.get('expected')!r} "
                f"stdout={first_fail.get('stdout')!r} stderr={first_fail.get('stderr')!r}"
            )
        user = (
            f"Problem: {title}\n"
            f"Concept: {concept}\nTags: {', '.join(tags)}\n"
            f"Passed {passed}/{total} all_passed={all_passed}\n"
            f"Statement (trim):\n{statement}\n\n"
            f"Source (trim):\n{str(source)[:2500]}\n\n"
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
        weak = [str(w).strip().lower() for w in (data.get("weak_concepts") or []) if str(w).strip()][:3]
        if not weak:
            weak = graded["weak_concepts"]
        graded = {
            "mentor_summary": str(data.get("mentor_summary") or "").strip() or graded["mentor_summary"],
            "weak_concepts": weak,
            "lesson": {
                "title": str(lesson_in.get("title") or graded["lesson"]["title"])[:120],
                "body": str(lesson_in.get("body") or graded["lesson"]["body"])[:2000],
                "try_this": str(lesson_in.get("try_this") or graded["lesson"]["try_this"])[:400],
            },
        }
    except Exception:
        # Best-effort LLM teach — heuristic graded above stays the response.
        pass

    next_id = pick_next_coding_id(
        db,
        exclude=assertion_id,
        weak_concepts=graded["weak_concepts"],
        tags=tags,
        concept=concept,
    )
    ref = str(payload.get("editor_solution") or "").strip()
    return {
        "mentor_summary": graded["mentor_summary"],
        "weak_concepts": graded["weak_concepts"],
        "lesson": graded["lesson"],
        "recommended_next_id": str(next_id) if next_id else None,
        "reference_solution": ref or None,
    }
