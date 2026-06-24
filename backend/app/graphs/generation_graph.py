"""Page-scoped MCQ generation from indexed PDF text."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.graphs.page_triage_graph import run_page_triage
from app.models import Document
from app.repositories.intel import update_activity
from app.services.mcq_dedup import prior_mcq_from_payload
from app.services.mcq_quality import generate_quality_mcq_batch
from app.services.question_pool import (
    clear_stale_coverage_complete,
    effective_question_budget,
    get_page_coverage,
    get_progress,
    mark_aspect_asked,
    mark_aspects_asked,
    on_batch_completed,
    set_coverage_complete,
)
from app.services.embed import embed_texts
from app.services.retrieval import fetch_chunks_for_page_range, search_chunks
from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens

# Stable full-page prefix for provider prompt caching; aspect hints live in the tail message.
_PAGE_CONTEXT_MAX_TOKENS = PAGE_INPUT_MAX_TOKENS


def _stable_page_context(page_text: str) -> str:
    """Byte-stable truncated page text — cacheable LLM prefix across batches."""
    return truncate_to_tokens(page_text, _PAGE_CONTEXT_MAX_TOKENS)


def get_cacheable_page_context(
    db: Session, document_id: uuid.UUID, page_number: int, page_text: str
) -> str:
    """Stable truncated page text, DB-cached so it survives worker restart and
    is shared across the 4 CPU workers.

    Replaces the old in-process dict: every worker used to compute and hold its
    own copy, and a crash dropped it — forcing the next batch on that page to
    re-truncate (cheap) but also lose provider-prompt-cache prefix stability
    hints across process boundaries. The DB cache is keyed per (doc, page).
    """
    from app.services.generation_cache import get as cache_get, page_context_key, put as cache_put

    key = page_context_key(document_id, page_number)
    cached = cache_get(db, kind="page_context", cache_key=key)
    if cached:
        return str(cached)
    ctx = _stable_page_context(page_text)
    cache_put(db, kind="page_context", cache_key=key, value=ctx)
    return ctx


def _aspect_retrieval_hints(
    db: Session,
    document_id: uuid.UUID,
    page_number: int,
    targets: list[dict[str, Any]],
    page_text: str,
) -> str:
    """Short per-aspect retrieval snippets for the variable tail message (not cached prefix)."""
    if not targets or not page_text:
        return ""

    try:
        labels = [str(t.get("label") or t.get("key") or "") for t in targets]
        vecs = embed_texts(labels)
        if not vecs:
            return ""
        dims = len(vecs[0])
        mean_vec = [sum(v[i] for v in vecs) / len(vecs) for i in range(dims)]
        hits = search_chunks(
            db,
            document_ids=[document_id],
            query_embedding=mean_vec,
            page_start=page_number,
            page_end=page_number,
            limit=5,
        )
        parts: list[str] = []
        seen: set[str] = set()
        for chunk in hits:
            t = (chunk.get("text") or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            parts.append(t[:600])
        return "\n---\n".join(parts)
    except Exception:
        return ""


def _page_content_hash(page_text: str) -> str:
    return hashlib.sha256((page_text or "").encode("utf-8")).hexdigest()


def _reusable_mcq_payloads(
    db: Session,
    *,
    page_hash: str,
    page_number: int,
    limit: int,
    exclude_artifact_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Reuse MCQs from other documents with identical page text."""
    from app.config import get_settings

    scope = (get_settings().mcq_reuse_scope or "all").lower()
    if scope == "off":
        return []

    demo_filter = "AND d.account_id IS NULL" if scope == "demo" else ""
    rows = db.execute(
        text(
            f"""
            SELECT a.payload
            FROM intel.assertion a
            JOIN qb.documents d ON d.id::text = a.payload->>'artifact_id'
            WHERE a.status = 'active'
              AND a.payload->>'page_content_hash' = :page_hash
              AND (a.payload->>'page_number')::int = :page
              AND a.payload->>'artifact_id' != :exclude_artifact
              {demo_filter}
            ORDER BY (a.payload->>'sequence')::int ASC
            LIMIT :limit
            """
        ),
        {
            "page_hash": page_hash,
            "page": page_number,
            "limit": limit,
            "exclude_artifact": str(exclude_artifact_id),
        },
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for raw in rows:
        payload = raw if isinstance(raw, dict) else json.loads(raw)
        if payload.get("question") and payload.get("options"):
            out.append(payload)
    return out


def _clone_reusable_mcqs(
    db: Session,
    document_id: uuid.UUID,
    *,
    page_number: int,
    page_hash: str,
    targets: list[dict[str, Any]],
    start_sequence: int,
    budget: int,
) -> int:
    reusable = _reusable_mcq_payloads(
        db,
        page_hash=page_hash,
        page_number=page_number,
        limit=len(targets),
        exclude_artifact_id=document_id,
    )
    if not reusable:
        return 0

    saved = 0
    sequence = start_sequence
    facet_rows: list[dict[str, Any]] = []
    asked_keys: list[str] = []
    payloads: list[tuple[dict[str, Any], int]] = []
    for target, template in zip(targets, reusable):
        sequence += 1
        if sequence > budget:
            break
        if _assertion_sequence_exists(db, document_id, page_number, sequence):
            continue
        payload = {
            k: v
            for k, v in template.items()
            if k not in {"artifact_id", "sequence", "quality"}
        }
        payload["page_content_hash"] = page_hash
        payload["reused_from_shared"] = True
        payloads.append((payload, sequence))
        if target and target.get("key"):
            asked_keys.append(str(target["key"]))
        saved += 1

    if payloads:
        _persist_assertions(
            db,
            document_id,
            payloads,
            page_number=page_number,
            facet_rows=facet_rows,
        )
    if asked_keys:
        mark_aspects_asked(db, document_id, page_number, asked_keys)
    if saved:
        from app.services.generation_checkpoint import checkpoint_after_save

        checkpoint_after_save(
            db,
            page_number=page_number,
            sequence=sequence,
            saved_total=saved,
        )
    return saved


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
    return {"questions_saved": 0, "error": f"unknown generation mode: {mode}"}


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


def _run_page_batch(db: Session, document_id: uuid.UUID, options: dict[str, Any]) -> dict[str, Any]:
    from app.services.generation_checkpoint import checkpoint_after_save, resolve_start_sequence

    activity_id = options.get("activity_id")
    page_number = int(options["page_number"])
    batch_size = int(options.get("batch_size", 5))
    start_sequence = resolve_start_sequence(
        db,
        document_id,
        page_number=page_number,
        options=options,
    )

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
    if not page_text:
        on_batch_completed(db, document_id, page=page_number, saved=0)
        db.commit()
        return {
            "questions_saved": 0,
            "page_number": page_number,
            "error": "page_not_indexed",
        }

    clear_stale_coverage_complete(db, document_id, page_number)
    doc = db.get(Document, document_id)
    if not doc:
        return {"questions_saved": 0, "page_number": page_number}

    progress = get_progress(doc)
    budget = effective_question_budget(doc, page_number, progress)

    remaining = budget - start_sequence
    targets = _next_aspects(doc, page_number, min(batch_size, max(0, remaining)))
    if not targets:
        coverage = get_page_coverage(doc, page_number)
        triage_ran = bool(coverage.get("aspects"))
        if triage_ran:
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
            on_batch_completed(db, document_id, page=page_number, saved=0)
            db.commit()
            return {"questions_saved": 0, "page_number": page_number, "error": "no_targets"}

    prior_mcqs = _prior_mcqs_on_page(db, document_id, page_number)
    page_hash = _page_content_hash(page_text)
    cloned = _clone_reusable_mcqs(
        db,
        document_id,
        page_number=page_number,
        page_hash=page_hash,
        targets=targets,
        start_sequence=start_sequence,
        budget=budget,
    )
    if cloned >= len(targets):
        on_batch_completed(db, document_id, page=page_number, saved=cloned)
        if activity_id:
            update_activity(
                db,
                uuid.UUID(str(activity_id)),
                status="succeeded",
                stats={"questions_saved": cloned, "page_number": page_number, "reused": True},
                finished=True,
            )
        db.commit()
        return {"questions_saved": cloned, "page_number": page_number, "reused": True}

    stable_context = get_cacheable_page_context(db, document_id, page_number, page_text)
    aspect_hints = _aspect_retrieval_hints(db, document_id, page_number, targets, page_text)
    kept = generate_quality_mcq_batch(
        db,
        page_text=stable_context,
        page_number=page_number,
        targets=targets,
        prior_mcqs=prior_mcqs,
        aspect_hints=aspect_hints,
    )

    saved = 0
    sequence = start_sequence
    facet_rows: list[dict[str, Any]] = []
    asked_keys: list[str] = []
    payloads_to_persist: list[tuple[dict[str, Any], int]] = []
    hit_budget = False
    for target, payload in kept:
        sequence += 1
        if sequence > budget:
            hit_budget = True
            break
        if _assertion_sequence_exists(db, document_id, page_number, sequence):
            continue
        payload = dict(payload)
        payload["page_content_hash"] = page_hash
        payloads_to_persist.append((payload, sequence))
        if target and target.get("key"):
            asked_keys.append(str(target["key"]))
        saved += 1

    if payloads_to_persist:
        _persist_assertions(
            db,
            document_id,
            payloads_to_persist,
            page_number=page_number,
            facet_rows=facet_rows,
        )
    if asked_keys:
        mark_aspects_asked(db, document_id, page_number, asked_keys)
    if saved:
        checkpoint_after_save(
            db,
            page_number=page_number,
            sequence=sequence,
            saved_total=saved,
        )
    if hit_budget:
        set_coverage_complete(db, document_id, page_number)

    if saved == 0 and targets:
        from app.services.mcq_quality import generate_quality_mcq
        from app.services.mcq_dedup import prior_mcq_from_payload

        for target in targets:
            if target.get("asked"):
                continue
            sequence += 1
            if sequence > budget:
                break
            if _assertion_sequence_exists(db, document_id, page_number, sequence):
                continue
            payload = generate_quality_mcq(
                db,
                page_text=stable_context,
                page_number=page_number,
                sequence=sequence,
                target_aspect=target,
                prior_mcqs=prior_mcqs,
            )
            if not payload:
                continue
            payload = dict(payload)
            payload["page_content_hash"] = page_hash
            _persist_assertion(
                db, document_id, payload, page_number=page_number, sequence=sequence
            )
            if target.get("key"):
                mark_aspect_asked(db, document_id, page_number, str(target["key"]))
            saved += 1
            # O(n^2) fix: mutate the in-memory list instead of re-querying all
            # MCQs on the page after every save. The next target's generation
            # sees this new question as a prior, which is all the DB query did.
            prior_mcqs = list(prior_mcqs or []) + [
                prior_mcq_from_payload(payload)
            ]
            checkpoint_after_save(
                db,
                page_number=page_number,
                sequence=sequence,
                saved_total=saved,
            )

    if saved == 0 and start_sequence + batch_size >= budget and not targets and bool(
        get_page_coverage(doc, page_number).get("aspects")
    ):
        set_coverage_complete(db, document_id, page_number)

    on_batch_completed(db, document_id, page=page_number, saved=saved)

    if activity_id:
        update_activity(
            db,
            uuid.UUID(str(activity_id)),
            status="succeeded" if saved > 0 or not targets else "failed",
            stats={"questions_saved": saved, "page_number": page_number},
            finished=True,
            error_summary=None if saved > 0 or not targets else "No questions passed quality gates",
        )
    db.commit()
    if saved == 0 and targets:
        return {"questions_saved": 0, "page_number": page_number, "error": "generation_empty"}
    return {"questions_saved": saved, "page_number": page_number}


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
    from app.services.mcq_assertion_facets import upsert_facet

    upsert_facet(
        db,
        assertion_id=assertion_id,
        artifact_id=document_id,
        page_number=page_number,
        sequence=sequence,
        payload=payload,
    )


def _persist_assertions(
    db: Session,
    document_id: uuid.UUID,
    payloads: list[tuple[dict[str, Any], int]],
    *,
    page_number: int,
    facet_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Batched persist — one executemany INSERT for assertions + one for facets.

    Replaces N sequential _persist_assertion calls (each = 1 assertion INSERT +
    1 facet UPSERT = 2N round trips for a batch of N) with two batched writes.
    Concept/source ids are resolved once. Returns the finalized payload dicts
    (with assertion_id set) so callers can append them to prior_mcqs in memory.
    """
    from app.repositories.intel import _concept_id, _source_id
    from app.services.mcq_assertion_facets import upsert_facets

    if not payloads:
        return []

    type_id = _concept_id(db, "/vocab/assertion/question.mcq")
    source_id = _source_id(db, "user-upload")

    assertion_rows: list[dict[str, Any]] = []
    finalized: list[dict[str, Any]] = []
    facet_input: list[dict[str, Any]] = []
    for payload, sequence in payloads:
        assertion_id = uuid.uuid4()
        payload = {
            **payload,
            "artifact_id": str(document_id),
            "format": "qb.mcq.v1",
            "page_number": page_number,
            "sequence": sequence,
        }
        assertion_rows.append(
            {
                "id": assertion_id,
                "type_id": type_id,
                "source_id": source_id,
                "uri": f"qb://assertion/{assertion_id}",
                "fp": f"{document_id}:{page_number}:{sequence}",
                "title": (payload.get("question") or "")[:200],
                "summary": payload.get("explanation"),
                "payload": json.dumps(payload),
            }
        )
        payload["_assertion_id"] = str(assertion_id)
        finalized.append(payload)
        facet_input.append(
            {
                "assertion_id": assertion_id,
                "artifact_id": document_id,
                "page_number": page_number,
                "sequence": sequence,
                "payload": payload,
            }
        )

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
        assertion_rows,
    )
    upsert_facets(db, facet_input)
    if facet_rows is not None:
        facet_rows.extend(facet_input)
    return finalized
