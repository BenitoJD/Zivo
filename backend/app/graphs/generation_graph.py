"""Page-scoped MCQ generation from indexed PDF text."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.graphs.page_triage_graph import run_page_triage
from app.models import Document
from app.repositories.intel import update_activity
from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt
from app.services.question_pool import (
    get_page_coverage,
    get_question_budget,
    mark_aspect_asked,
    on_batch_completed,
    set_coverage_complete,
)
from app.services.retrieval import fetch_chunks_for_page_range


def run_generation(db: Session, document_id: uuid.UUID, options: dict[str, Any]) -> dict[str, Any]:
    mode = options.get("mode", "page_batch")
    if mode == "page_triage":
        return run_page_triage(
            db,
            document_id,
            page_number=int(options["page_number"]),
            activity_id=options.get("activity_id"),
        )
    if mode == "page_batch":
        return _run_page_batch(db, document_id, options)
    return _run_legacy_pool(db, document_id, options)


def _next_aspect(doc: Document, page_number: int) -> dict[str, Any] | None:
    cov = get_page_coverage(doc, page_number)
    for aspect in cov.get("aspects") or []:
        if not aspect.get("asked"):
            return aspect
    return None


def _asked_aspect_labels(doc: Document, page_number: int) -> list[str]:
    labels: list[str] = []
    for aspect in get_page_coverage(doc, page_number).get("aspects") or []:
        if aspect.get("asked"):
            labels.append(str(aspect.get("label") or aspect.get("key")))
    return labels


def _run_page_batch(db: Session, document_id: uuid.UUID, options: dict[str, Any]) -> dict[str, Any]:
    activity_id = options.get("activity_id")
    page_number = int(options["page_number"])
    batch_size = int(options.get("batch_size", 5))
    start_sequence = int(options.get("start_sequence", 0))

    doc = db.get(Document, document_id)
    if not doc:
        return {"questions_saved": 0, "page_number": page_number}

    chunks = fetch_chunks_for_page_range(
        db,
        document_ids=[document_id],
        page_start=page_number,
        page_end=page_number,
    )
    page_text = "\n\n".join(c["text"] for c in chunks if c.get("text")).strip()
    budget = get_question_budget(doc, page_number)

    saved = 0
    for offset in range(batch_size):
        sequence = start_sequence + offset + 1
        if sequence > budget:
            set_coverage_complete(db, document_id, page_number)
            break

        target = _next_aspect(doc, page_number)
        if not target:
            set_coverage_complete(db, document_id, page_number)
            break

        payload = _generate_one_for_page(
            db,
            page_text=page_text,
            page_number=page_number,
            sequence=sequence,
            document_id=document_id,
            target_aspect=target,
            asked_labels=_asked_aspect_labels(doc, page_number),
        )
        _persist_assertion(db, document_id, payload, page_number=page_number, sequence=sequence)
        mark_aspect_asked(db, document_id, page_number, str(target["key"]))
        db.refresh(doc)
        saved += 1
        if activity_id:
            update_activity(
                db,
                uuid.UUID(str(activity_id)),
                stats={
                    "questions_saved": saved,
                    "page_number": page_number,
                    "batch_size": batch_size,
                },
            )

    if saved == 0 and start_sequence + batch_size >= budget:
        set_coverage_complete(db, document_id, page_number)

    on_batch_completed(db, document_id, page=page_number, saved=saved)

    if activity_id:
        update_activity(
            db,
            uuid.UUID(str(activity_id)),
            status="succeeded",
            stats={"questions_saved": saved, "page_number": page_number},
            finished=True,
        )
    db.commit()
    return {"questions_saved": saved, "page_number": page_number}


def _run_legacy_pool(db: Session, document_id: uuid.UUID, options: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible small pool (unused in normal flow)."""
    activity_id = options.get("activity_id")
    pool_size = int(options.get("pool_size", 5))
    saved = 0
    for i in range(pool_size):
        payload = _generate_one_for_page(
            db,
            page_text="",
            page_number=1,
            sequence=i + 1,
            document_id=document_id,
        )
        _persist_assertion(db, document_id, payload, page_number=1, sequence=i + 1)
        saved += 1
    if activity_id:
        update_activity(
            db,
            uuid.UUID(str(activity_id)),
            status="succeeded",
            stats={"questions_saved": saved},
            finished=True,
        )
    db.commit()
    return {"questions_saved": saved}


def _complete_chat_sync(db: Session, messages: list[dict]) -> str:
    return asyncio.run(complete_chat(messages, db, log_tag="generate_mcq"))


def _parse_mcq_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text_block = raw.strip()
    fence = re.search(r"```(?:zv-mcq|json)?\s*(\{.*?\})\s*```", text_block, re.DOTALL)
    if fence:
        text_block = fence.group(1)
    elif text_block.startswith("{"):
        pass
    else:
        start = text_block.find("{")
        end = text_block.rfind("}")
        if start >= 0 and end > start:
            text_block = text_block[start : end + 1]
        else:
            return None
    try:
        return json.loads(text_block)
    except json.JSONDecodeError:
        return None


def _normalize_mcq_payload(data: dict[str, Any], target_aspect: dict[str, Any] | None) -> dict[str, Any]:
    question = data.get("question") or data.get("stem") or ""
    options = data.get("options") or data.get("choices") or []
    if not question or len(options) < 2:
        raise ValueError("invalid mcq")
    key = data.get("primary_concept_key") or (target_aspect or {}).get("key") or "page-concept"
    label = data.get("primary_concept") or (target_aspect or {}).get("label") or "Page concept"
    return {
        "question": question,
        "options": options,
        "correct_index": int(data.get("correct_index", 0)),
        "explanation": data.get("explanation") or "",
        "primary_concept_key": key,
        "primary_concept": label,
        "tags": data.get("tags") or ["auto"],
    }


def _generate_one_for_page(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    sequence: int,
    document_id: uuid.UUID,
    target_aspect: dict[str, Any] | None = None,
    asked_labels: list[str] | None = None,
) -> dict[str, Any]:
    excerpt = page_text[:12_000] if page_text else ""
    aspect_line = ""
    if target_aspect:
        aspect_line = (
            f"\nTarget this aspect only: {target_aspect.get('label')} "
            f"(key: {target_aspect.get('key')}).\n"
        )
    if asked_labels:
        aspect_line += f"\nAlready asked (do not repeat): {', '.join(asked_labels[:20])}.\n"

    if excerpt:
        try:
            system = get_prompt(db, "mcq_format")
            user = (
                f"Generate exactly one multiple-choice question from this PDF page.\n"
                f"Page number: {page_number}\n"
                f"Question index on this page: {sequence}\n"
                f"{aspect_line}\n"
                f"Page text:\n{excerpt}\n\n"
                "Return only one ```zv-mcq``` JSON block. Include primary_concept_key matching the target aspect."
            )
            raw = _complete_chat_sync(db, [{"role": "system", "content": system}, {"role": "user", "content": user}])
            parsed = _parse_mcq_json(raw)
            if parsed:
                payload = _normalize_mcq_payload(parsed, target_aspect)
                payload["page_number"] = page_number
                payload["sequence"] = sequence
                return payload
        except Exception:
            pass

    preview = excerpt[:240].replace("\n", " ").strip() if excerpt else "the selected page"
    label = (target_aspect or {}).get("label") or f"Page {page_number}"
    key = (target_aspect or {}).get("key") or f"page-{page_number}"
    return {
        "question": f"According to page {page_number}, which statement best reflects: {label}?",
        "options": [
            f"A key idea about {label}",
            "An unrelated detail from another section",
            "The opposite of what the page states",
            "A plausible but unsupported claim",
        ],
        "correct_index": 0,
        "explanation": f"Review page {page_number}: {preview}…",
        "primary_concept_key": key,
        "primary_concept": label,
        "tags": ["fallback"],
        "page_number": page_number,
        "sequence": sequence,
    }


def _persist_assertion(
    db: Session,
    document_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    page_number: int,
    sequence: int,
) -> None:
    from app.repositories.intel import _concept_id, _source_id

    assertion_id = uuid.uuid4()
    payload = {
        **payload,
        "artifact_id": str(document_id),
        "format": "qb.mcq.v1",
        "page_number": page_number,
        "sequence": sequence,
    }
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
            "type_id": _concept_id(db, "/vocab/assertion/question.mcq"),
            "source_id": _source_id(db, "user-upload"),
            "uri": f"qb://assertion/{assertion_id}",
            "fp": f"{document_id}:{page_number}:{sequence}",
            "title": (payload.get("question") or "")[:200],
            "summary": payload.get("explanation"),
            "payload": json.dumps(payload),
        },
    )
