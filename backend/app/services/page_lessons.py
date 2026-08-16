"""Per-page Learn lesson storage + cook-time orchestration.

A Learn lesson is short, question-aware teaching prose shown before a page's
MCQs (see :mod:`app.graphs.lesson_graph`). Unlike the on-demand study artifacts
(notes / flashcards / explanations), a lesson is generated **inside the page
cook** — before the MCQ loop — so it is ready by the time the learner reaches
the questions. There is no read-path enqueue and no separate worker job; the
cook calls :func:`cook_page_lesson` directly.

Persistence reuses :class:`ArtifactStore` for the load/save/status primitives
(same upsert pattern as every ``qb.document_*`` artifact table). The lesson's
own twist on the shared skeleton:

  * **per-page staleness** — ``content_hash`` (the page-text sha256 the MCQ
    pipeline already computes) is stamped into the row. On cook, a ready row
    whose hash still matches the live page text is reused as-is, so re-cooking
    an unchanged page is free.
  * **policy_version in payload** — the lesson JSONB carries the voice/structure
    version (``qb.lesson.v1``) so a prompt change can invalidate old lessons by
    bumping the constant in :mod:`lesson_graph`.

The cook is best-effort: any failure is caught, the row is marked ``failed``,
and generation continues to the MCQ loop. The learner simply does not see a
lesson for that page — they are never blocked.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.services.artifact_store import ArtifactStore, coerce_jsonb

logger = logging.getLogger(__name__)

LESSON_ASPECT_FALLBACK = "- the main idea of this page"
# Caps so a runaway model cannot store a wall of text where a brief belongs.
LESSON_TITLE_MAX_CHARS = 160
LESSON_BODY_MAX_CHARS = 2400


@dataclass(frozen=True)
class LessonAspectPlan:
    lines: tuple[str, ...]
    fallback: str = LESSON_ASPECT_FALLBACK
    policy_version: str = "qb.lesson.v1"

    def as_block(self) -> str:
        return "\n".join(self.lines) if self.lines else self.fallback


def _aspect_is_central(aspect: dict[str, Any]) -> bool:
    from app.services.aspect_discovery import parse_centrality

    is_central = aspect.get("central")
    if is_central is not None:
        return bool(is_central)
    raw = aspect.get("centrality")
    if raw is None:
        return False
    return parse_centrality(raw) == "central"


def _aspect_prompt_line(aspect: dict[str, Any]) -> str | None:
    label = str(aspect.get("label") or "").strip()
    if not label:
        return None
    angle = str(aspect.get("cognitive_angle") or "").strip()
    suffix = f" ({angle})" if angle and angle.lower() != "recall" else ""
    return f"- {label}{suffix}"


def plan_lesson_aspects(aspects: Sequence[dict[str, Any]] | None) -> LessonAspectPlan:
    """Only central aspects are taught; recall angle is the default (omitted)."""
    lines: list[str] = []
    for aspect in aspects or []:
        if not _aspect_is_central(aspect):
            continue
        line = _aspect_prompt_line(aspect)
        if line:
            lines.append(line)
    return LessonAspectPlan(tuple(lines))


def should_cook_lesson(*, serve_mode: str, aspects: Sequence[dict[str, Any]] | None) -> bool:
    """Learn-only: teach central aspects before the MCQ loop. Test never pre-teaches."""
    return serve_mode == "learn" and bool(aspects)


@dataclass(frozen=True)
class LessonOutputCaps:
    title_chars: int
    body_chars: int
    policy_version: str = "qb.lesson.v1"


def plan_lesson_output_caps() -> LessonOutputCaps:
    """Truncate generated lesson title and body before persist."""
    return LessonOutputCaps(LESSON_TITLE_MAX_CHARS, LESSON_BODY_MAX_CHARS)


# Natural key (document_id, page_number); payload is the JSONB `lesson` object;
# `content_hash` is an extra non-key column written on every upsert so a ready
# row can be cheaply checked for staleness against the live page-text hash.
_LESSON_STORE = ArtifactStore(
    table="qb.page_lessons",
    key_cols=["page_number"],
    payload_col="lesson",
    extra_cols={"content_hash": "content_hash"},
)


def _row_to_state(row: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a raw page_lessons row into the queue payload shape, or null."""
    if not row:
        return {"status": "missing", "title": None, "body": None}
    lesson = coerce_jsonb(row.get("lesson")) or {}
    return {
        "status": row.get("status") or "missing",
        "title": lesson.get("title"),
        "body": lesson.get("body"),
    }


def get_lesson(db: Session, document_id: uuid.UUID, page: int) -> dict[str, Any] | None:
    """Read path for the learn-queue payload.

    Returns ``{"status", "title", "body"}`` where ``status`` is one of
    ``missing|generating|ready|failed``. The frontend shows the lesson only when
    ``status == "ready"`` and a non-empty body is present; otherwise it falls
    straight through to the MCQs (or a brief preparing state while generating).
    """
    row = _LESSON_STORE.load_row(db, document_id, page_number=page)
    state = _row_to_state(row)
    if state["status"] != "ready" or not state.get("body"):
        return None
    return state


def cook_page_lesson(
    db: Session,
    document_id: uuid.UUID,
    *,
    page: int,
    page_text: str,
    aspects: list[dict[str, Any]],
    content_hash: str,
) -> None:
    """Generate and persist one page's lesson, or reuse the cached row if fresh.

    Called from the page cook (``_run_page_batch``) before the MCQ loop, gated
    to Learn mode by the caller. Idempotent: a ready row whose ``content_hash``
    matches the live page text is returned untouched. Best-effort: any failure
    is logged and the row is marked ``failed`` — generation flow is never blocked.
    """
    from app.graphs.lesson_graph import LESSON_POLICY_VERSION, generate_lesson

    row = _LESSON_STORE.load_row(db, document_id, page_number=page)
    if (
        row
        and row.get("status") == "ready"
        and row.get("content_hash") == content_hash
        and (coerce_jsonb(row.get("lesson")) or {}).get("body")
    ):
        # Page text unchanged and a lesson already exists — reuse it.
        return

    _LESSON_STORE.set_status(
        db, document_id, "generating", page_number=page, content_hash=content_hash
    )
    db.commit()
    try:
        result = generate_lesson(db, page_text=page_text, aspects=aspects)
    except Exception as exc:  # noqa: BLE001 — best-effort; never block the cook
        logger.warning(
            "lesson generation failed doc=%s page=%s: %s", document_id, page, exc, exc_info=True
        )
        _LESSON_STORE.set_status(
            db,
            document_id,
            "failed",
            error=str(exc)[:500],
            page_number=page,
            content_hash=content_hash,
        )
        db.commit()
        return
    if not result or not result.get("body"):
        _LESSON_STORE.set_status(
            db, document_id, "failed", error="empty_lesson", page_number=page, content_hash=content_hash
        )
        db.commit()
        return
    payload = {
        "title": result["title"],
        "body": result["body"],
        "policy_version": LESSON_POLICY_VERSION,
    }
    _LESSON_STORE.save(
        db, document_id, payload, page_number=page, content_hash=content_hash
    )
    db.commit()
