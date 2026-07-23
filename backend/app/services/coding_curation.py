"""Human-curated coding problem bank — create/update/seed without LLM generation.

Complements ``coding_generation`` (which writes problems from uploaded material).
Curated problems use the same ``intel.assertion`` + ``qb.coding_assertion_facets``
shape (``format: qb.coding.v1``) so run/submit and the public list stay shared.

Hidden tests + editorial reference stay server-side; public reads still go through
``public_payload``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.intel import _concept_id, _source_id
from app.services.code_execution import DEFAULT_LANGUAGE_ID, LANGUAGES
from app.services.coding_generation import _split_tests, public_payload

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    s = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return (s or "problem")[:80]


def _normalize_difficulty(raw: str | None) -> str:
    d = (raw or "medium").strip().lower()
    return d if d in ("easy", "medium", "hard") else "medium"


def _normalize_tags(tags: list[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in tags or []:
        label = str(t).strip().lower()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label[:40])
        if len(out) >= 12:
            break
    return out


def _normalize_tests(tests: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    clean: list[dict[str, str]] = []
    for t in tests or []:
        if not isinstance(t, dict):
            continue
        clean.append(
            {
                "stdin": str(t.get("stdin", "")),
                "expected_output": str(t.get("expected_output", t.get("expected", ""))),
            }
        )
    return clean


def build_full_payload(
    *,
    title: str,
    statement: str,
    starter_code: str,
    difficulty: str,
    language_id: int,
    sample_tests: list[dict[str, str]],
    hidden_tests: list[dict[str, str]],
    concept: str = "",
    tags: list[str] | None = None,
    editor_solution: str = "",
    artifact_id: str | None = None,
    page_number: int = 0,
    sequence: int = 0,
) -> dict[str, Any]:
    return {
        "format": "qb.coding.v1",
        "artifact_id": artifact_id,
        "page_number": page_number,
        "sequence": sequence,
        "title": title,
        "statement": statement,
        "starter_code": starter_code or "",
        "language_id": language_id,
        "language_label": LANGUAGES.get(language_id, "Python (3.8)"),
        "difficulty": difficulty,
        "sample_tests": sample_tests,
        "hidden_tests": hidden_tests,
        "editor_solution": editor_solution or "",
        "concept": concept or "",
        "tags": tags or [],
        "origin": "curated",
    }


def upsert_curated_problem(
    db: Session,
    *,
    title: str,
    statement: str,
    starter_code: str,
    tests: list[dict[str, Any]],
    difficulty: str = "medium",
    language_id: int = DEFAULT_LANGUAGE_ID,
    concept: str = "",
    tags: list[str] | None = None,
    editor_solution: str = "",
    published: bool = True,
    assertion_id: uuid.UUID | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    """Insert or update one curated coding assertion + facet row.

    Requires ≥1 hidden test (after sample split). Returns public payload + id.
    """
    title_clean = (title or "").strip()[:200] or "Untitled"
    statement_clean = (statement or "").strip()
    if not statement_clean:
        raise ValueError("statement is required")

    clean_tests = _normalize_tests(tests)
    sample_tests, hidden_tests = _split_tests(clean_tests)
    if not hidden_tests:
        raise ValueError("Need at least 3 tests (2 sample + 1 hidden) to grade")

    difficulty_clean = _normalize_difficulty(difficulty)
    tags_clean = _normalize_tags(tags)
    lang = int(language_id or DEFAULT_LANGUAGE_ID)
    if lang not in LANGUAGES:
        lang = DEFAULT_LANGUAGE_ID

    fp_slug = slug or _slugify(title_clean)
    fingerprint = f"coding:curated:{fp_slug}"

    full_payload = build_full_payload(
        title=title_clean,
        statement=statement_clean,
        starter_code=starter_code,
        difficulty=difficulty_clean,
        language_id=lang,
        sample_tests=sample_tests,
        hidden_tests=hidden_tests,
        concept=concept.strip() if concept else "",
        tags=tags_clean,
        editor_solution=editor_solution,
    )

    type_id = _concept_id(db, "/vocab/assertion/question.coding")
    source_id = _source_id(db, "coding-bank")

    if assertion_id is None:
        # Idempotent seed/create by fingerprint when slug is stable.
        existing = db.execute(
            text("SELECT id FROM intel.assertion WHERE fingerprint = :fp LIMIT 1"),
            {"fp": fingerprint},
        ).scalar()
        if existing:
            assertion_id = uuid.UUID(str(existing))

    if assertion_id is None:
        assertion_id = uuid.uuid4()
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
                "type_id": type_id,
                "source_id": source_id,
                "uri": f"qb://assertion/{assertion_id}",
                "fp": fingerprint,
                "title": title_clean,
                "summary": statement_clean[:500],
                "payload": json.dumps(full_payload),
            },
        )
    else:
        # Keep existing fingerprint so title edits don't collide with other rows.
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
                "summary": statement_clean[:500],
                "payload": json.dumps(full_payload),
                "source_id": source_id,
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
              :aid, NULL, 0, 0,
              :title, :difficulty, :language_id,
              :sample_count, :hidden_count,
              :published, 'curated', :tags
            )
            ON CONFLICT (assertion_id) DO UPDATE SET
              artifact_id = NULL,
              title = EXCLUDED.title,
              difficulty = EXCLUDED.difficulty,
              language_id = EXCLUDED.language_id,
              sample_test_count = EXCLUDED.sample_test_count,
              hidden_test_count = EXCLUDED.hidden_test_count,
              published = EXCLUDED.published,
              origin = 'curated',
              tags = EXCLUDED.tags
            """
        ),
        {
            "aid": assertion_id,
            "title": title_clean,
            "difficulty": difficulty_clean,
            "language_id": lang,
            "sample_count": len(sample_tests),
            "hidden_count": len(hidden_tests),
            "published": bool(published),
            "tags": tags_clean,
        },
    )

    pub = public_payload(full_payload)
    pub["id"] = str(assertion_id)
    pub["published"] = bool(published)
    pub["origin"] = "curated"
    return pub


def editorial_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Admin/editor view — includes hidden tests + reference solution."""
    return {
        **public_payload(payload),
        "hidden_tests": payload.get("hidden_tests") or [],
        "editor_solution": payload.get("editor_solution") or "",
        "origin": payload.get("origin") or "generated",
    }


def set_published(db: Session, assertion_id: uuid.UUID, published: bool) -> None:
    row = db.execute(
        text(
            """
            UPDATE qb.coding_assertion_facets
            SET published = :published
            WHERE assertion_id = :aid
            RETURNING assertion_id
            """
        ),
        {"aid": assertion_id, "published": published},
    ).first()
    if not row:
        raise LookupError("Not found")


def soft_delete_curated(db: Session, assertion_id: uuid.UUID) -> None:
    """Unpublish + mark assertion inactive (keeps measurement history)."""
    db.execute(
        text(
            """
            UPDATE intel.assertion SET status = 'inactive'
            WHERE id = :id AND (payload->>'format') = 'qb.coding.v1'
            """
        ),
        {"id": assertion_id},
    )
    db.execute(
        text(
            """
            UPDATE qb.coding_assertion_facets
            SET published = false
            WHERE assertion_id = :aid
            """
        ),
        {"aid": assertion_id},
    )


# ---------------------------------------------------------------- starter bank


_STARTER_PROBLEMS: list[dict[str, Any]] = [
    {
        "slug": "sum-two-integers",
        "title": "Sum Two Integers",
        "difficulty": "easy",
        "concept": "I/O basics",
        "tags": ["basics", "arithmetic"],
        "statement": (
            "Read two integers from stdin (space-separated on one line) and print their sum.\n\n"
            "Input format:\n"
            "  A single line: a b\n\n"
            "Output format:\n"
            "  A single integer — a + b\n\n"
            "Example:\n"
            "  Input: 3 5\n"
            "  Output: 8"
        ),
        "starter_code": "a, b = map(int, input().split())\nprint(0)\n",
        "editor_solution": "a, b = map(int, input().split())\nprint(a + b)\n",
        "tests": [
            {"stdin": "3 5\n", "expected_output": "8\n"},
            {"stdin": "0 0\n", "expected_output": "0\n"},
            {"stdin": "-2 7\n", "expected_output": "5\n"},
            {"stdin": "1000000 2000000\n", "expected_output": "3000000\n"},
            {"stdin": "-10 -20\n", "expected_output": "-30\n"},
        ],
    },
    {
        "slug": "fizzbuzz-range",
        "title": "FizzBuzz Range",
        "difficulty": "easy",
        "concept": "Conditionals",
        "tags": ["basics", "loops"],
        "statement": (
            "Read an integer n. For each i from 1 to n inclusive, print one line:\n"
            "  - \"FizzBuzz\" if i is divisible by 3 and 5\n"
            "  - \"Fizz\" if divisible by 3 only\n"
            "  - \"Buzz\" if divisible by 5 only\n"
            "  - the number itself otherwise\n\n"
            "Input: one integer n (1 ≤ n ≤ 100)\n"
            "Output: n lines as described.\n\n"
            "Example (n=5):\n"
            "1\n2\nFizz\n4\nBuzz"
        ),
        "starter_code": "n = int(input())\nfor i in range(1, n + 1):\n    print(i)\n",
        "editor_solution": (
            "n = int(input())\n"
            "for i in range(1, n + 1):\n"
            "    if i % 15 == 0:\n"
            "        print('FizzBuzz')\n"
            "    elif i % 3 == 0:\n"
            "        print('Fizz')\n"
            "    elif i % 5 == 0:\n"
            "        print('Buzz')\n"
            "    else:\n"
            "        print(i)\n"
        ),
        "tests": [
            {"stdin": "5\n", "expected_output": "1\n2\nFizz\n4\nBuzz\n"},
            {"stdin": "1\n", "expected_output": "1\n"},
            {
                "stdin": "15\n",
                "expected_output": (
                    "1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n"
                    "11\nFizz\n13\n14\nFizzBuzz\n"
                ),
            },
            {"stdin": "3\n", "expected_output": "1\n2\nFizz\n"},
            {"stdin": "10\n", "expected_output": "1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n"},
        ],
    },
    {
        "slug": "reverse-words",
        "title": "Reverse Words",
        "difficulty": "medium",
        "concept": "Strings",
        "tags": ["strings", "arrays"],
        "statement": (
            "Read one line of text. Reverse the order of words (whitespace-separated) "
            "and print them on one line, single-space separated. Leading/trailing "
            "whitespace is ignored; internal runs of spaces collapse to one.\n\n"
            "Example:\n"
            "  Input:  hello   world from zivo\n"
            "  Output: zivo from world hello"
        ),
        "starter_code": "s = input()\nprint(s)\n",
        "editor_solution": "print(' '.join(input().split()[::-1]))\n",
        "tests": [
            {"stdin": "hello world\n", "expected_output": "world hello\n"},
            {"stdin": "a\n", "expected_output": "a\n"},
            {"stdin": "  one   two  three \n", "expected_output": "three two one\n"},
            {"stdin": "zivo from world hello\n", "expected_output": "hello world from zivo\n"},
            {"stdin": "keep order almost\n", "expected_output": "almost order keep\n"},
        ],
    },
    {
        "slug": "valid-parentheses",
        "title": "Valid Parentheses",
        "difficulty": "medium",
        "concept": "Stacks",
        "tags": ["stack", "strings"],
        "statement": (
            "Read one string consisting only of the characters ()[]{}. "
            "Print \"true\" if the brackets are balanced and correctly nested, "
            "otherwise print \"false\".\n\n"
            "Example:\n"
            "  Input: ([])\n"
            "  Output: true\n\n"
            "  Input: ([)]\n"
            "  Output: false"
        ),
        "starter_code": "s = input().strip()\nprint('false')\n",
        "editor_solution": (
            "s = input().strip()\n"
            "pairs = {')': '(', ']': '[', '}': '{'}\n"
            "stack = []\n"
            "ok = True\n"
            "for ch in s:\n"
            "    if ch in '([{':\n"
            "        stack.append(ch)\n"
            "    elif ch in pairs:\n"
            "        if not stack or stack[-1] != pairs[ch]:\n"
            "            ok = False\n"
            "            break\n"
            "        stack.pop()\n"
            "    else:\n"
            "        ok = False\n"
            "        break\n"
            "print('true' if ok and not stack else 'false')\n"
        ),
        "tests": [
            {"stdin": "()\n", "expected_output": "true\n"},
            {"stdin": "([])\n", "expected_output": "true\n"},
            {"stdin": "([)]\n", "expected_output": "false\n"},
            {"stdin": "{[()]}\n", "expected_output": "true\n"},
            {"stdin": "(((\n", "expected_output": "false\n"},
            {"stdin": "\n", "expected_output": "true\n"},
        ],
    },
]


def seed_starter_bank(db: Session) -> dict[str, Any]:
    """Idempotently upsert the classic starter problems. Returns counts."""
    created = 0
    updated = 0
    for spec in _STARTER_PROBLEMS:
        before = db.execute(
            text("SELECT id FROM intel.assertion WHERE fingerprint = :fp"),
            {"fp": f"coding:curated:{spec['slug']}"},
        ).scalar()
        upsert_curated_problem(
            db,
            title=spec["title"],
            statement=spec["statement"],
            starter_code=spec["starter_code"],
            tests=spec["tests"],
            difficulty=spec["difficulty"],
            concept=spec.get("concept") or "",
            tags=spec.get("tags") or [],
            editor_solution=spec.get("editor_solution") or "",
            published=True,
            slug=spec["slug"],
        )
        if before:
            updated += 1
        else:
            created += 1
    return {"created": created, "updated": updated, "total": len(_STARTER_PROBLEMS)}
