"""On-demand question generation for the public practice library.

Given a Wikidata QID, ensure enough practice questions exist for that concept:
if the concept already has enough active questions, return 'ready'; otherwise
fetch the concept's Wikipedia article, store it as a platform-owned public
Document, and enqueue the existing generation pipeline against it.

The taxonomy is Wikidata-by-reference (see services/wikidata.py). The Document
is marked `meta.is_public = True` so can_access_document() treats it as open to
everyone, and `meta.wikidata_qid` lets us reuse it on repeat visits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.repositories.intel import (
    count_concept_questions,
    create_activity,
    get_or_create_concept_entity,
    get_concept_entity_by_qid,
    link_concept_parent,
)
from app.services import wikidata
from app.services.jobs import enqueue_generate
from app.services.question_budget import (
    PRACTICE_CONCEPT_MIN_QUESTIONS,
    plan_practice_concept_ready,
    plan_practice_source_chunk_chars,
)

DEFAULT_MIN_QUESTIONS = PRACTICE_CONCEPT_MIN_QUESTIONS


@dataclass
class EnsureOutcome:
    status: str  # "ready" | "generating"
    qid: str
    label: str
    question_count: int
    job_id: str | None = None
    error: str | None = None


def concept_status(db: Session, qid: str) -> EnsureOutcome:
    """Cheap read: how many questions exist for this concept right now?

    Does NOT touch the network. Used for polling and the concept-page UI.
    """
    qid = (qid or "").strip().upper()
    entity_id = get_concept_entity_by_qid(db, qid)
    count = count_concept_questions(db, entity_id) if entity_id else 0
    return EnsureOutcome(
        status="ready" if count > 0 else "empty",
        qid=qid,
        label="",
        question_count=count,
    )


def ensure_concept_questions(
    db: Session,
    qid: str,
    min_count: int = DEFAULT_MIN_QUESTIONS,
) -> EnsureOutcome:
    """Idempotently ensure a concept has >= min_count practice questions.

    Steps:
      1. Resolve the concept + its parents from Wikidata; materialize intel.entity
         rows for them and record the subclass_of hierarchy edges.
      2. If enough questions already exist → return 'ready'.
      3. Reuse an existing public Document for this QID if one exists; otherwise
         fetch the Wikipedia article and create a new public Document + chunks.
      4. Enqueue the standard generation job (mode page_batch, practice_qid pinned)
         so the existing pipeline — quality gate, dedup, tagging — runs unchanged.
    """
    qid = (qid or "").strip().upper()
    if not qid.startswith("Q"):
        return EnsureOutcome(status="ready", qid=qid, label="", question_count=0, error="invalid_qid")

    # 1. Resolve concept + hierarchy from Wikidata (network).
    try:
        detail = _run_async(wikidata.get_concept(qid))
    except wikidata.WikidataError as exc:
        return EnsureOutcome(status="ready", qid=qid, label="", question_count=0, error=str(exc))

    concept_entity_id = get_or_create_concept_entity(db, qid, detail.label, detail.description)
    for parent in detail.parents:
        parent_entity_id = get_or_create_concept_entity(db, parent.qid, parent.label, parent.description)
        link_concept_parent(db, concept_entity_id, parent_entity_id)
    db.flush()

    # 2. Enough questions already?
    existing = count_concept_questions(db, concept_entity_id)
    if plan_practice_concept_ready(existing=existing, min_count=min_count):
        return EnsureOutcome(
            status="ready", qid=qid, label=detail.label, question_count=existing
        )

    # 3. Find or create a public Document sourced from the Wikipedia article.
    doc = _find_public_doc_for_qid(db, qid)
    if doc is None:
        try:
            article = _run_async(wikidata.get_wikipedia_article(qid))
        except wikidata.WikidataError as exc:
            return EnsureOutcome(
                status="ready", qid=qid, label=detail.label,
                question_count=existing, error=str(exc),
            )
        doc = _create_public_doc_from_article(db, article)

    # 4. Enqueue generation. The job runs the full quality pipeline; the
    #    pinned_qid flows into _persist_assertions so every output is tagged.
    activity_id = create_activity(
        db,
        type_uri="/vocab/activity/generate_practice",
        agent="api.practice.generate",
        source_slug="wikipedia",
        stats={"qid": qid, "label": detail.label, "document_id": str(doc.id)},
    )
    job = enqueue_generate(
        db,
        document_id=doc.id,
        account_id=None,
        activity_id=activity_id,
        options={
            "mode": "page_batch",
            "page_number": 1,
            "batch_size": min_count,
            "practice_qid": qid,
        },
    )
    db.commit()
    return EnsureOutcome(
        status="generating",
        qid=qid,
        label=detail.label,
        question_count=existing,
        job_id=str(job.id),
    )


def _find_public_doc_for_qid(db: Session, qid: str) -> Document | None:
    """Return an existing public practice Document for this QID, if any."""
    from sqlalchemy import text

    row = db.execute(
        text(
            """
            SELECT id FROM documents
            WHERE meta->>'is_public' = 'true'
              AND meta->>'wikidata_qid' = :qid
              AND account_id IS NULL
            LIMIT 1
            """
        ),
        {"qid": qid},
    ).first()
    if not row:
        return None
    return db.get(Document, row[0])


def _create_public_doc_from_article(db: Session, article: wikidata.WikipediaArticle) -> Document:
    """Store a Wikipedia article as a platform-owned public Document + chunks."""
    doc_id = uuid.uuid4()
    safe_title = (article.title or article.qid).replace(" ", "-").lower()[:80]
    text_body = article.text or ""
    # Split into page-sized chunks so the page-scoped generator has something to chew on.
    chunks = _split_text(text_body, plan_practice_source_chunk_chars())
    if not chunks:
        chunks = [text_body]

    doc = Document(
        id=doc_id,
        account_id=None,
        slug=f"wiki-{article.qid.lower()}-{safe_title}",
        filename=f"{safe_title}.txt",
        content_type="text/plain",
        size_bytes=len(text_body),
        storage_key=f"practice/{article.qid}/{safe_title}.txt",
        status="ready",
        index_progress=100,
        meta={
            "is_public": True,
            "wikidata_qid": article.qid,
            "source": "wikipedia",
            "source_url": article.source_url,
            "title": article.title,
            "page_count": len(chunks),
        },
    )
    db.add(doc)
    for i, chunk_text in enumerate(chunks, start=1):
        db.add(
            DocumentChunk(
                document_id=doc_id,
                page_start=i,
                page_end=i,
                text=chunk_text,
                meta={},
            )
        )
    db.flush()
    return doc


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split on paragraph boundaries, then merge until ~max_chars per chunk."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else []
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paragraphs:
        if size + len(para) > max_chars and buf:
            chunks.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += len(para)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _run_async(coro):
    """Run an async coroutine from a sync context (the API worker is sync).

    Safe when there is no running event loop in this thread, which is the case
    for FastAPI sync endpoints and the CPU worker. Reuses a fresh loop each call.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # We're inside an existing loop (e.g. async endpoint). This sync helper
        # shouldn't be called from there; fall back to a thread to avoid blocking.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


__all__ = [
    "EnsureOutcome",
    "concept_status",
    "ensure_concept_questions",
    "DEFAULT_MIN_QUESTIONS",
]
