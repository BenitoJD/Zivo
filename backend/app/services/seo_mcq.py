"""Attach or generate MCQs for an SEO post."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Document
from app.repositories import seo as seo_repo
from app.services.mcq_quality import generate_quality_mcq
from app.services.seo_gate import (
    evaluate_seo_mcq_attach_ready,
    plan_seo_mcq_attach_defaults,
    plan_seo_mcq_page_chars,
    plan_seo_related_mcq_query_limit,
)

logger = logging.getLogger(__name__)


def find_related_assertions(
    db: Session,
    *,
    document_id: uuid.UUID | None,
    limit: int | None = None,
) -> list[uuid.UUID]:
    if not document_id:
        return []
    rows = db.execute(
        text(
            """
            SELECT a.id
            FROM intel.assertion a
            JOIN intel.concept c ON c.id = a.type_concept_id
            WHERE a.payload->>'artifact_id' = :aid
              AND a.status = 'active'
              AND c.uri = '/vocab/assertion/question.mcq'
            ORDER BY a.recorded_at ASC
            LIMIT :lim
            """
        ),
        {"aid": str(document_id), "lim": plan_seo_related_mcq_query_limit(limit)},
    ).all()
    return [uuid.UUID(str(r[0])) for r in rows]


def find_diverse_edition_assertions(
    db: Session,
    *,
    document_id: uuid.UUID,
    min_count: int | None = None,
    max_count: int | None = None,
) -> list[uuid.UUID]:
    """Pick MCQs from the edition pool with distinct primary_concept_key when possible."""
    rows = db.execute(
        text(
            """
            SELECT a.id,
                   COALESCE(NULLIF(a.payload->>'primary_concept_key', ''), a.id::text) AS ck
            FROM intel.assertion a
            JOIN intel.concept c ON c.id = a.type_concept_id
            WHERE a.payload->>'artifact_id' = :aid
              AND a.status = 'active'
              AND c.uri = '/vocab/assertion/question.mcq'
            ORDER BY a.recorded_at ASC
            """
        ),
        {"aid": str(document_id)},
    ).mappings().all()
    if not rows:
        return []

    from app.services.seo_gate import plan_seo_mcq_attach, plan_seo_mcq_attach_defaults

    attach = plan_seo_mcq_attach_defaults()
    lo = attach.min_count if min_count is None else int(min_count)
    hi = attach.max_count if max_count is None else int(max_count)
    planned = plan_seo_mcq_attach(
        [(uuid.UUID(str(row["id"])), str(row["ck"] or "")) for row in rows],
        min_count=lo,
        max_count=hi,
    )
    return planned


def ensure_public_artifact(
    db: Session,
    *,
    post_id: uuid.UUID,
    slug: str,
    body_md: str,
) -> uuid.UUID:
    """Create/reuse a public document so guest MCQ grade can resolve access."""
    existing = db.execute(
        text(
            """
            SELECT id FROM qb.documents
            WHERE meta->>'seo_post_id' = :pid
            LIMIT 1
            """
        ),
        {"pid": str(post_id)},
    ).first()
    if existing:
        return uuid.UUID(str(existing[0]))

    doc_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        account_id=None,
        slug=f"seo-{slug}"[:64],
        filename=f"{slug}.md",
        content_type="text/markdown",
        size_bytes=len(body_md.encode("utf-8")),
        storage_key=f"seo/{post_id}/{slug}.md",
        status="ready",
        index_progress=100,
        meta={
            "is_public": True,
            "hide_source": True,
            "seo_blog": True,
            "seo_post_id": str(post_id),
            "title": slug,
            "page_count": 1,
        },
    )
    db.add(doc)
    db.flush()
    db.execute(
        text(
            """
            INSERT INTO qb.document_chunks (document_id, page_start, page_end, text, meta)
            VALUES (:d, 1, 1, :t, '{}'::jsonb)
            """
        ),
        {"d": doc_id, "t": body_md[:20000]},
    )
    seo_repo.set_post_artifact(db, post_id, doc_id)
    return doc_id


def _persist_mcq(
    db: Session,
    *,
    document_id: uuid.UUID,
    draft: dict[str, Any],
    sequence: int,
) -> uuid.UUID:
    from app.repositories.intel import _concept_id, _source_id

    assertion_id = uuid.uuid4()
    payload = {
        **draft,
        "artifact_id": str(document_id),
        "format": "qb.mcq.v1",
        "page_number": 1,
        "sequence": sequence,
    }
    # Normalize stem key
    if "question" in payload and "stem" not in payload:
        payload["stem"] = payload["question"]
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
            "fp": f"seo:{document_id}:{sequence}",
            "title": (payload.get("stem") or payload.get("question") or "")[:200],
            "summary": payload.get("explanation"),
            "payload": json.dumps(payload),
        },
    )
    return assertion_id


def attach_or_generate_mcqs(
    db: Session,
    *,
    post_id: uuid.UUID,
    slug: str,
    body_md: str,
    source_document_id: uuid.UUID | None = None,
    target_count: int | None = None,
) -> list[uuid.UUID]:
    """Prefer existing related assertions; else generate from rewritten post."""
    attach = plan_seo_mcq_attach_defaults()
    n = attach.target_count if target_count is None else int(target_count)
    attached = find_related_assertions(
        db, document_id=source_document_id, limit=n
    )
    if evaluate_seo_mcq_attach_ready(len(attached)):
        seo_repo.attach_assertions(db, post_id, attached[:n])
        return attached[:n]

    artifact_id = ensure_public_artifact(
        db, post_id=post_id, slug=slug, body_md=body_md
    )
    ids = list(attached)
    prior: list[dict[str, Any]] = []
    for seq in range(len(ids) + 1, n + 1):
        try:
            draft = generate_quality_mcq(
                db,
                page_text=body_md[: plan_seo_mcq_page_chars()],
                page_number=1,
                sequence=seq,
                prior_mcqs=prior,
                max_attempts=attach.generation_attempts,
            )
        except Exception:
            logger.exception("seo mcq generate failed seq=%s", seq)
            draft = None
        if not draft:
            continue
        prior.append(draft)
        try:
            aid = _persist_mcq(db, document_id=artifact_id, draft=draft, sequence=seq)
            ids.append(aid)
        except Exception:
            logger.exception("seo mcq persist failed")
    if ids:
        seo_repo.attach_assertions(db, post_id, ids)
    return ids


def attach_edition_mcqs(
    db: Session,
    *,
    post_id: uuid.UUID,
    document_id: uuid.UUID,
    min_count: int | None = None,
    max_count: int | None = None,
) -> list[uuid.UUID]:
    """Attach concept-diverse MCQs from the edition assertion pool."""
    attach = plan_seo_mcq_attach_defaults()
    lo = attach.min_count if min_count is None else int(min_count)
    hi = attach.max_count if max_count is None else int(max_count)
    ids = find_diverse_edition_assertions(
        db,
        document_id=document_id,
        min_count=lo,
        max_count=hi,
    )
    if ids:
        seo_repo.attach_assertions(db, post_id, ids)
    return ids
