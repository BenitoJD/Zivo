"""CPU workload handlers."""

import logging
from uuid import UUID

from app.db import SessionLocal
from app.eta.registry import eta
from app.models import Document, JobPriority, JobWorkload
from app.services.chunking import chunk_pages
from app.services.chunks import finalize_image_document, persist_document_index, upsert_page_chunks
from app.services.embed import embed_texts
from app.services.jobs import batch_enqueue_jobs, enqueue_job
from app.services.parse import document_bytes_hash, parse_document, parse_document_page
from app.services.rag_window import (
    chat_rag_window,
    refresh_rag_window_status,
    save_rag_window,
    sync_rag_window,
)
from app.services.question_pool import get_progress, selected_page_list
from app.services.storage import delete_object, fetch_object, get_json, ingest_tmp_key, put_json


logger = logging.getLogger(__name__)

@eta(name="ingest.parse_document", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def parse_document_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}
        raw = fetch_object(doc.storage_key)
        if doc.content_type.startswith("image/"):
            finalize_image_document(db, document_id)
            return {"document_id": str(document_id), "image": True}

        pages = parse_document(doc.content_type, raw)
        total_pages = len(pages)
        selected = (doc.meta or {}).get("selected_range")
        if selected:
            page_list = selected.get("pages")
            if isinstance(page_list, list) and page_list:
                allowed = {int(p) for p in page_list}
                pages = [p for p in pages if int(p.get("page", 0)) in allowed]
            else:
                page_from = int(selected.get("from", 1))
                page_to = int(selected.get("to", page_from))
                pages = [p for p in pages if page_from <= int(p.get("page", 0)) <= page_to]
        meta = dict(doc.meta or {})
        meta["page_count"] = total_pages or 1
        doc.meta = meta
        put_json(ingest_tmp_key(document_id, "pages"), {"pages": pages})
        doc.index_progress = 30
        db.commit()
        enqueue_job(
            db,
            name="ingest.chunk_pages",
            workload=JobWorkload.cpu,
            payload={"document_id": str(document_id)},
        )
    return {"document_id": str(document_id), "pages": len(pages)}


@eta(name="ingest.chunk_pages", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def chunk_pages_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    pages_data = get_json(ingest_tmp_key(document_id, "pages"))
    pages = pages_data.get("pages", [])
    chunks = chunk_pages(pages)
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if doc:
            doc.index_progress = 50
            db.commit()
    put_json(ingest_tmp_key(document_id, "chunks"), {"chunks": chunks})
    with SessionLocal() as db:
        enqueue_job(
            db,
            name="ingest.embed_chunks",
            workload=JobWorkload.cpu,
            payload={"document_id": str(document_id)},
        )
    return {"document_id": str(document_id), "chunks": len(chunks)}


@eta(name="ingest.embed_chunks", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def embed_chunks_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    chunks_data = get_json(ingest_tmp_key(document_id, "chunks"))
    chunks = chunks_data.get("chunks", [])
    texts = [f"passage: {c['text']}" for c in chunks]
    vectors = embed_texts(texts) if texts else []

    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if doc:
            doc.index_progress = 80
            db.commit()
        count = persist_document_index(db, document_id, chunks, vectors)

        from app.services.question_generation import enqueue_generate_if_needed

        enqueue_generate_if_needed(db, document_id)

    for suffix in ("pages", "chunks"):
        try:
            delete_object(ingest_tmp_key(document_id, suffix))
        except Exception:
            logger.debug("ingest temp-object cleanup failed", exc_info=True)

    return {"document_id": str(document_id), "chunks": count}


@eta(name="ingest.document", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def ingest_document_job(payload: dict) -> dict:
    """Bundled ingest: parse → chunk → embed → persist in ONE job.

    The happy path used to fan out across three separate CPU jobs
    (parse_document → chunk_pages → embed_chunks), each costing a worker poll
    cycle before the next could start. On a single-worker VPS that's two extra
    queue hops (up to ~2 × POLL_INTERVAL each) before generation can begin.
    Bundling collapses them to one hop while keeping the granular handlers
    registered for the retry/legacy paths and existing tests.
    """
    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}
        raw = fetch_object(doc.storage_key)
        if doc.content_type.startswith("image/"):
            finalize_image_document(db, document_id)
            return {"document_id": str(document_id), "image": True}

        # --- parse ---
        pages = parse_document(doc.content_type, raw)
        total_pages = len(pages)
        selected = (doc.meta or {}).get("selected_range")
        if selected:
            page_list = selected.get("pages")
            if isinstance(page_list, list) and page_list:
                allowed = {int(p) for p in page_list}
                pages = [p for p in pages if int(p.get("page", 0)) in allowed]
            else:
                page_from = int(selected.get("from", 1))
                page_to = int(selected.get("to", page_from))
                pages = [p for p in pages if page_from <= int(p.get("page", 0)) <= page_to]
        meta = dict(doc.meta or {})
        meta["page_count"] = total_pages or 1
        doc.meta = meta
        doc.index_progress = 40
        db.commit()

    # --- chunk ---
    chunks = chunk_pages(pages)
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if doc:
            doc.index_progress = 60
            db.commit()

    # --- embed + persist ---
    texts = [f"passage: {c['text']}" for c in chunks]
    vectors = embed_texts(texts) if texts else []
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if doc:
            doc.index_progress = 90
            db.commit()
        count = persist_document_index(db, document_id, chunks, vectors)

        from app.services.question_generation import enqueue_generate_if_needed

        enqueue_generate_if_needed(db, document_id)

    return {"document_id": str(document_id), "chunks": count}


@eta(name="ingest.page", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def ingest_page_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    page_number = int(payload["page_number"])
    chunks: list[dict] = []
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}
        raw = fetch_object(doc.storage_key)
        cache_key = ingest_tmp_key(document_id, "parsed_pages")
        content_hash = document_bytes_hash(raw)
        cached_pages: list[dict] | None = None
        try:
            cached = get_json(cache_key)
            if cached.get("content_hash") == content_hash and isinstance(cached.get("pages"), list):
                cached_pages = cached["pages"]
        except Exception:
            cached_pages = None
        if cached_pages is None and not (
            doc.content_type.startswith("application/pdf") or raw[:4] == b"%PDF"
        ):
            cached_pages = parse_document(doc.content_type, raw)
            put_json(cache_key, {"content_hash": content_hash, "pages": cached_pages})
        page = parse_document_page(
            doc.content_type,
            raw,
            page_number,
            cached_pages=cached_pages,
        )
        # Vision OCR fallback: scanned/image pages with no extractable text
        # would otherwise be lost as blank. Render the page and transcribe it
        # with a vision LLM (cached per page, never re-bills, degrades to
        # blank on failure). Gated so the flag controls cost.
        from app.config import get_settings

        if (
            get_settings().vision_ocr_enabled
            and len((page.get("text") or "").strip()) < 40
            and (doc.content_type.startswith("application/pdf") or raw[:4] == b"%PDF")
        ):
            from app.services.vision_ocr import transcribe_page_with_vision

            ocr_text = transcribe_page_with_vision(db, document_id, page_number)
            if ocr_text:
                page["text"] = ocr_text
        chunks = chunk_pages([page])
        texts = [f"passage: {c['text']}" for c in chunks if c.get("text")]
        vectors = embed_texts(texts) if texts else []
        if chunks and vectors:
            upsert_page_chunks(db, document_id, page_number, chunks, vectors)
        # Always mark ingest complete — empty pages write no chunks, but triage/RAG
        # must still treat them as ready (otherwise scans defer forever).
        from app.services.rag_window import mark_page_ingested

        mark_page_ingested(db, doc, page_number)
        db.commit()
        from app.services.background_prep import is_background_prep, on_page_ingested_for_prep

        if is_background_prep(doc):
            on_page_ingested_for_prep(db, document_id)
        else:
            refresh_rag_window_status(db, document_id)
            from app.services.question_pool import maybe_enqueue_early_page_triage

            maybe_enqueue_early_page_triage(db, document_id, page_number=page_number)
    return {"document_id": str(document_id), "page_number": page_number, "chunks": len(chunks)}


@eta(name="ingest.rag_window", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def ingest_rag_window_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}
        progress = get_progress(doc)
        current = int(payload.get("current_page") or progress.get("current_page") or 1)
        study = selected_page_list(doc)
        target = chat_rag_window(current, study)
        save_rag_window(db, doc, target)
        # Only take the document back to "indexing" for the INITIAL window. Once a
        # doc is "ready" the learner is actively studying it (questions already
        # exist), and a sliding-window re-index for chat context must not flip the
        # whole document back to "indexing" — that makes the study UI flash its
        # full-screen "loading N%" takeover on refresh. The window's readiness is
        # tracked independently via meta["rag_window_ready"] (popped by
        # save_rag_window above), which gates chat without disrupting the queue.
        if doc.status != "ready":
            doc.status = "indexing"
        db.commit()
        pages_to_ingest = sync_rag_window(db, document_id, target)
        db.commit()
        if pages_to_ingest:
            batch_enqueue_jobs(
                db,
                [
                    {
                        "name": "ingest.page",
                        "workload": JobWorkload.cpu,
                        "payload": {
                            "document_id": str(document_id),
                            "page_number": page,
                        },
                    }
                    for page in pages_to_ingest
                ],
                chunk_size=50,
            )
        if not pages_to_ingest:
            refresh_rag_window_status(db, document_id)
    return {
        "document_id": str(document_id),
        "pages": target,
        "ingest_queued": pages_to_ingest,
    }


@eta(name="ingest.full_range", workload=JobWorkload.cpu, priority=JobPriority.LOW)
def ingest_full_range_job(payload: dict) -> dict:
    from app.services.background_prep import is_background_prep, start_full_range_ingest

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}
        if not is_background_prep(doc):
            return {"skipped": True, "reason": "not_background_prep"}
        progress = get_progress(doc)
        current = int(payload.get("current_page") or progress.get("current_page") or 1)
        missing = start_full_range_ingest(db, doc, current_page=current)
    return {
        "document_id": str(document_id),
        "ingest_queued": missing,
    }


@eta(name="learn.transition_prep", workload=JobWorkload.cpu)
def transition_prep_job(payload: dict) -> dict:
    from app.services.question_pool import (
        FIRST_QUESTION_BATCH_SIZE,
        REFILL_BATCH_SIZE,
        count_assertions_on_page,
        effective_question_budget,
        enqueue_page_batch,
        enqueue_page_triage,
        get_page_coverage,
        get_progress,
        get_question_budget,
        mark_transition_prep_done,
        selected_page_list,
    )
    from app.services.session_design import evaluate_serve_schedule

    document_id = UUID(payload["document_id"])
    current_page = int(payload["current_page"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}

        progress = get_progress(doc)
        budget = effective_question_budget(doc, current_page, progress)
        generated = count_assertions_on_page(db, document_id, current_page)
        if generated < budget:
            remaining = budget - generated
            # Rolling refill only — never one job for the entire remaining plan.
            enqueue_page_batch(
                db,
                doc,
                page=current_page,
                batch_size=min(REFILL_BATCH_SIZE, remaining),
                start_sequence=generated,
            )

        study = selected_page_list(doc)
        next_page: int | None = None
        if current_page in study:
            idx = study.index(current_page)
            if idx < len(study) - 1:
                next_page = study[idx + 1]

        answered_on_page = int(progress.get("answered_on_page") or 0)
        triage_budget = get_question_budget(doc, current_page)
        allow_next_page_generation = evaluate_serve_schedule(
            answered_on_page=answered_on_page,
            page_budget=triage_budget,
        ).generate_next_page if triage_budget > 0 else False

        if next_page is not None:
            if not get_page_coverage(doc, next_page):
                enqueue_page_triage(db, doc, page=next_page)
            elif (
                allow_next_page_generation
                and count_assertions_on_page(db, document_id, next_page) == 0
            ):
                next_budget = effective_question_budget(doc, next_page, progress)
                enqueue_page_batch(
                    db,
                    doc,
                    page=next_page,
                    batch_size=min(FIRST_QUESTION_BATCH_SIZE, next_budget),
                    start_sequence=0,
                )
            enqueue_job(
                db,
                name="ingest.rag_window",
                workload=JobWorkload.cpu,
                payload={"document_id": str(document_id), "current_page": next_page},
            )

        mark_transition_prep_done(db, doc, current_page)
        db.commit()

    return {"document_id": str(document_id), "current_page": current_page, "next_page": next_page}


@eta(name="generate.questions", workload=JobWorkload.cpu, priority=JobPriority.HIGH)
def generate_questions_job(payload: dict) -> dict:
    from app.graphs.generation_graph import run_generation
    from app.services.question_pool import on_batch_failed

    document_id = UUID(payload["document_id"])
    page_number = int(payload.get("page_number") or 0)
    with SessionLocal() as db:
        try:
            return run_generation(db, document_id, payload)
        except Exception:
            on_batch_failed(db, document_id, page=page_number)
            raise


@eta(name="generate.coding", workload=JobWorkload.cpu)
def generate_coding_job(payload: dict) -> dict:
    """Generate LeetCode-style coding problems for one programmable page.

    Spawned by ``_maybe_spawn_coding`` when page triage flags a page as
    ``programmable``. Each problem is generate→verify→persist: the LLM's own
    reference solution must pass every hidden test in the sandbox, or the problem
    is discarded and retried (then dropped) — silence beats a broken problem.
    """
    from app.services.coding_generation import generate_coding_for_page
    from app.services.retrieval import fetch_chunks_for_page_range

    document_id = UUID(payload["document_id"])
    page_number = int(payload.get("page_number") or 0)
    count = int(payload.get("count") or 1)
    with SessionLocal() as db:
        chunks = fetch_chunks_for_page_range(
            db,
            document_ids=[document_id],
            page_start=page_number,
            page_end=page_number,
        )
        page_text = "\n\n".join(c.get("text", "") for c in chunks if c.get("text")).strip()
        if not page_text:
            return {"document_id": str(document_id), "page_number": page_number, "saved": 0}
        saved = generate_coding_for_page(
            db, document_id, page_number=page_number, page_text=page_text, count=count
        )
        return {
            "document_id": str(document_id),
            "page_number": page_number,
            "saved": len(saved),
            "problems": saved,
        }


@eta(name="generate.debug", workload=JobWorkload.cpu)
def generate_debug_job(payload: dict) -> dict:
    """Cook debug diagnostic scenarios from a cook job or document page."""
    from app.services.debug_generation import generate_debug_for_page, run_cook_job

    cook_job_id = payload.get("cook_job_id")
    if cook_job_id:
        with SessionLocal() as db:
            result = run_cook_job(db, UUID(cook_job_id))
            return result

    document_id = UUID(payload["document_id"])
    page_number = int(payload.get("page_number") or 0)
    count = int(payload.get("count") or 2)
    with SessionLocal() as db:
        from app.services.retrieval import fetch_chunks_for_page_range

        chunks = fetch_chunks_for_page_range(
            db,
            document_ids=[document_id],
            page_start=page_number,
            page_end=page_number,
        )
        page_text = "\n\n".join(c.get("text", "") for c in chunks if c.get("text")).strip()
        if not page_text:
            return {"document_id": str(document_id), "page_number": page_number, "saved": 0}
        saved = generate_debug_for_page(
            db, document_id, page_number=page_number, page_text=page_text, count=count
        )
        return {
            "document_id": str(document_id),
            "page_number": page_number,
            "saved": saved,
        }

