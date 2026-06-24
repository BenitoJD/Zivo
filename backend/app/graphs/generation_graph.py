"""Page-scoped MCQ generation from indexed PDF text."""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.graphs.page_triage_graph import run_page_triage
from app.models import Document
from app.repositories.intel import update_activity
from app.services.mcq_dedup import prior_mcq_from_payload
from app.services.mcq_quality import generate_quality_mcq, generate_quality_mcq_batch
from app.services.question_pool import (
    get_page_coverage,
    get_question_budget,
    mark_aspect_asked,
    on_batch_completed,
    set_coverage_complete,
)
from app.services.embed import embed_texts
from app.services.retrieval import fetch_chunks_for_page_range, search_chunks
from app.services.mcq_dedup import prior_mcq_from_payload

# How many questions in a batch to generate concurrently. The model is a hosted
# API (serves concurrent requests), so a batch's wall-clock collapses from the
# sum of per-question times to roughly the slowest single question. Bounded to
# stay under the provider's per-key rate limit; override with the env var.
GENERATION_CONCURRENCY = int(os.getenv("ZIVO_GENERATION_CONCURRENCY", "5"))

# Retrieval-first context: instead of sending the whole page (~12k chars) to the
# model, embed the target aspect labels and retrieve only the semantically
# relevant chunks. ~75% less input processing = much faster generation, and
# tighter/grounded questions (less noise for the model to wade through). The
# fallback is the full page text if retrieval returns too little.
CONTEXT_CHAR_BUDGET = int(os.getenv("ZIVO_CONTEXT_CHAR_BUDGET", "4000"))
CONTEXT_MIN_CHUNKS = 3


def _fetch_focused_context(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    targets: list[dict[str, Any]],
    page_text: str,
) -> str:
    """Aspect-relevant page context for generation, instead of the whole page.

    Embeds the target aspect labels, retrieves the top matching chunks via
    pgvector, and concatenates up to CONTEXT_CHAR_BUDGET chars. Falls back to the
    full page text if retrieval misses (so generation never fails for lack of
    context — the model always has something grounded to work from).
    """
    if not targets:
        return page_text[:12_000]

    try:
        # Embed all aspect labels in one batch; use the mean vector as the query
        # so we retrieve chunks relevant to the WHOLE set, not just one aspect.
        labels = [str(t.get("label") or t.get("key") or "") for t in targets]
        vecs = embed_texts(labels)
        if not vecs:
            return page_text[:12_000]
        dims = len(vecs[0])
        mean_vec = [sum(v[i] for v in vecs) / len(vecs) for i in range(dims)]
        hits = search_chunks(
            db,
            document_ids=[document_id],
            query_embedding=mean_vec,
            page_start=page_number,
            page_end=page_number,
            limit=10,
        )
        # Dedupe by text, keep order by score, accumulate up to the char budget.
        seen: set[str] = set()
        parts: list[str] = []
        total = 0
        for chunk in hits:
            t = (chunk.get("text") or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            if total + len(t) > CONTEXT_CHAR_BUDGET and len(parts) >= CONTEXT_MIN_CHUNKS:
                break
            parts.append(t)
            total += len(t)
        if len(parts) < CONTEXT_MIN_CHUNKS:
            # Retrieval too sparse — fall back to the full page so the model
            # still has grounded context to write from.
            return page_text[:12_000]
        return "\n\n".join(parts)
    except Exception:
        # Any retrieval failure must not block generation.
        return page_text[:12_000]


def run_generation(db: Session, document_id: uuid.UUID, options: dict[str, Any]) -> dict[str, Any]:
    mode = options.get("mode", "page_batch")
    if mode == "page_triage":
        return run_page_triage(
            db,
            document_id,
            page_number=int(options["page_number"]),
            activity_id=options.get("activity_id"),
            precompute=bool(options.get("precompute")),
        )
    if mode == "page_batch":
        return _run_page_batch(db, document_id, options)
    return _run_legacy_pool(db, document_id, options)


def _assertion_sequence_exists(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    sequence: int,
) -> bool:
    exists = db.execute(
        text(
            """
            SELECT 1 FROM intel.assertion
            WHERE payload->>'artifact_id' = :artifact_id
              AND status = 'active'
              AND (payload->>'page_number')::int = :page
              AND (payload->>'sequence')::int = :sequence
            LIMIT 1
            """
        ),
        {
            "artifact_id": str(document_id),
            "page": page_number,
            "sequence": sequence,
        },
    ).scalar()
    return exists is not None


def _next_aspect(doc: Document, page_number: int) -> dict[str, Any] | None:
    cov = get_page_coverage(doc, page_number)
    for aspect in cov.get("aspects") or []:
        if not aspect.get("asked"):
            return aspect
    return None


def _prior_mcqs_on_page(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT payload FROM intel.assertion
            WHERE payload->>'artifact_id' = :artifact_id
              AND status = 'active'
              AND (payload->>'page_number')::int = :page
            ORDER BY (payload->>'sequence')::int ASC
            """
        ),
        {"artifact_id": str(document_id), "page": page_number},
    ).scalars().all()
    prior: list[dict[str, Any]] = []
    for raw in rows:
        payload = raw if isinstance(raw, dict) else json.loads(raw)
        prior.append(prior_mcq_from_payload(payload))
    return prior


def _next_aspects(doc: Document, page_number: int, n: int) -> list[dict[str, Any]]:
    """The next up-to-n aspects on this page that have not been asked yet."""
    cov = get_page_coverage(doc, page_number)
    out: list[dict[str, Any]] = []
    for aspect in cov.get("aspects") or []:
        if not aspect.get("asked"):
            out.append(aspect)
            if len(out) >= n:
                break
    return out


def _speculative_aspects(page_text: str, page_number: int, n: int) -> list[dict[str, Any]]:
    """Lightweight aspect targets when triage hasn't landed yet.

    Mirrors the heuristic in page_triage_graph._fallback_triage (paragraph-based)
    so the first batch can start generating immediately instead of blocking on
    the triage LLM call. Real triage overwrites page_coverage later and refines
    the plan for subsequent batches — the speculative aspects only seed the
    first questions. Each target is marked ``speculative=True`` so it's clear
    in coverage which aspects came from the fast path.
    """
    if not page_text:
        return []
    paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()]
    n = max(1, n)
    targets: list[dict[str, Any]] = []
    for i, para in enumerate(paragraphs[:n]):
        label = para[:120].replace("\n", " ")
        targets.append(
            {
                "key": f"page-{page_number}-spec-{i + 1}",
                "label": label,
                "asked": False,
                "answered": False,
                "speculative": True,
            }
        )
    if not targets:
        targets.append(
            {
                "key": f"page-{page_number}-spec-main",
                "label": "Main ideas on this page",
                "asked": False,
                "answered": False,
                "speculative": True,
            }
        )
    return targets


def _generate_batch_parallel(
    *,
    page_text: str,
    page_number: int,
    targets: list[dict[str, Any]],
    prior_mcqs: list[dict[str, Any]],
    start_sequence: int,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Generate one MCQ per target concurrently.

    Each task gets its own DB session (Sessions are not thread-safe); the hosted
    model serves the calls in parallel, so the batch's wall-clock is ~one question
    instead of the sum. Returns (target, payload) pairs for successful generations.
    """
    if not targets:
        return []

    def _work(item: tuple[int, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        idx, target = item
        thread_db = SessionLocal()
        try:
            payload = generate_quality_mcq(
                thread_db,
                page_text=page_text,
                page_number=page_number,
                sequence=start_sequence + idx + 1,
                target_aspect=target,
                prior_mcqs=prior_mcqs,
            )
            return target, payload
        finally:
            thread_db.close()

    max_workers = max(1, min(len(targets), GENERATION_CONCURRENCY))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_work, enumerate(targets)))
    return [(t, p) for t, p in results if p is not None]


def _reconcile_batch(
    results: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Drop intra-batch near-duplicates.

    Parallel questions can't see each other's output, so each was deduped only
    against prior questions on the page. Reconcile the new batch against itself.

    Embeds once for the whole batch (vs one embed call per item): we embed every
    candidate signature up front, then compare in-memory, collapsing N embed
    round-trips to one.
    """
    from app.services.embed import embed_texts
    from app.services.mcq_dedup import cosine_similarity, mcq_signature

    if not results:
        return []
    if len(results) == 1:
        return list(results)

    # Embed all candidate signatures in a single batch call.
    sigs = [mcq_signature(payload) for _, payload in results]
    vectors = embed_texts(sigs)

    kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
    kept_vectors: list[list[float]] = []
    for (target, payload), vec in zip(results, vectors, strict=True):
        duplicate = False
        for kept_vec in kept_vectors:
            if cosine_similarity(vec, kept_vec) >= 0.92:
                duplicate = True
                break
        if duplicate:
            continue
        kept.append((target, payload))
        kept_vectors.append(vec)
    return kept


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

    remaining = budget - start_sequence
    targets = _next_aspects(doc, page_number, min(batch_size, max(0, remaining)))
    if not targets:
        # No unasked aspects. If triage has already run (coverage exists), the
        # page is genuinely complete. If triage hasn't landed yet, synthesize
        # speculative aspect targets from the page text so the first batch can
        # start immediately instead of blocking on the triage LLM call. Real
        # triage overwrites coverage afterward and refines later batches.
        coverage = get_page_coverage(doc, page_number)
        triage_ran = bool(coverage.get("aspects")) or coverage.get("coverage_complete")
        if triage_ran or not page_text:
            set_coverage_complete(db, document_id, page_number)
            on_batch_completed(db, document_id, page=page_number, saved=0)
            if activity_id:
                update_activity(
                    db,
                    uuid.UUID(str(activity_id)),
                    status="succeeded",
                    stats={"questions_saved": 0, "page_number": page_number},
                    finished=True,
                )
            db.commit()
            return {"questions_saved": 0, "page_number": page_number}
        targets = _speculative_aspects(
            page_text, page_number, min(batch_size, max(0, remaining))
        )
        if not targets:
            set_coverage_complete(db, document_id, page_number)
            on_batch_completed(db, document_id, page=page_number, saved=0)
            db.commit()
            return {"questions_saved": 0, "page_number": page_number}

    prior_mcqs = _prior_mcqs_on_page(db, document_id, page_number)
    # Retrieval-first: instead of the whole page, send only the chunks relevant
    # to these target aspects. ~75% less input for the model to process, and
    # tighter/grounded questions. Falls back to full page text if retrieval
    # returns too little.
    context = _fetch_focused_context(db, document_id, page_number, targets, page_text)
    # One LLM call produces up to N MCQs (N = len(targets), capped at 5). The
    # batch generator runs the per-MCQ quality gate (heuristics + embedding
    # similarity) internally, so this replaces both the N-way parallel
    # _generate_batch_parallel AND the post-hoc _reconcile_batch dedup. A single
    # call collapses N TTFTs to one and lets the model self-coordinate distractor
    # diversity across the batch.
    kept = generate_quality_mcq_batch(
        db,
        page_text=context,
        page_number=page_number,
        targets=targets,
        prior_mcqs=prior_mcqs,
    )

    saved = 0
    sequence = start_sequence
    for target, payload in kept:
        sequence += 1
        if sequence > budget:
            set_coverage_complete(db, document_id, page_number)
            break
        if _assertion_sequence_exists(db, document_id, page_number, sequence):
            continue
        _persist_assertion(db, document_id, payload, page_number=page_number, sequence=sequence)
        if target and target.get("key"):
            mark_aspect_asked(db, document_id, page_number, str(target["key"]))
        saved += 1

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
        if payload is None:
            continue
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


def _generate_one_for_page(
    db: Session,
    *,
    page_text: str,
    page_number: int,
    sequence: int,
    document_id: uuid.UUID,
    target_aspect: dict[str, Any] | None = None,
    prior_mcqs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    payload = generate_quality_mcq(
        db,
        page_text=page_text,
        page_number=page_number,
        sequence=sequence,
        target_aspect=target_aspect,
        prior_mcqs=prior_mcqs,
    )
    if payload is None:
        return None
    payload["page_number"] = page_number
    payload["sequence"] = sequence
    return payload


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
