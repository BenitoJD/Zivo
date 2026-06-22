"""CPU workload handlers."""

from uuid import UUID

from app.db import SessionLocal
from app.eta.registry import eta
from app.models import Document, JobWorkload
from app.services.chunking import chunk_pages
from app.services.chunks import finalize_image_document, persist_document_index, upsert_page_chunks
from app.services.embed import embed_texts
from app.services.jobs import enqueue_job
from app.services.parse import parse_document, parse_document_page
from app.services.rag_window import (
    chat_rag_window,
    refresh_rag_window_status,
    save_rag_window,
    sync_rag_window,
)
from app.services.question_pool import get_progress, selected_page_list
from app.services.storage import delete_object, fetch_object, get_json, ingest_tmp_key, put_json


@eta(name="ingest.parse_document", workload=JobWorkload.cpu)
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
        meta["page_count"] = int(meta.get("page_count") or total_pages or 1)
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


@eta(name="ingest.chunk_pages", workload=JobWorkload.cpu)
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


@eta(name="ingest.embed_chunks", workload=JobWorkload.cpu)
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
            pass

    return {"document_id": str(document_id), "chunks": count}


@eta(name="ingest.page", workload=JobWorkload.cpu)
def ingest_page_job(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    page_number = int(payload["page_number"])
    chunks: list[dict] = []
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}
        raw = fetch_object(doc.storage_key)
        page = parse_document_page(doc.content_type, raw, page_number)
        chunks = chunk_pages([page])
        texts = [f"passage: {c['text']}" for c in chunks if c.get("text")]
        vectors = embed_texts(texts) if texts else []
        if chunks and vectors:
            upsert_page_chunks(db, document_id, page_number, chunks, vectors)
        db.commit()
        refresh_rag_window_status(db, document_id)
    return {"document_id": str(document_id), "page_number": page_number, "chunks": len(chunks)}


@eta(name="ingest.rag_window", workload=JobWorkload.cpu)
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
        doc.status = "indexing"
        db.commit()
        pages_to_ingest = sync_rag_window(db, document_id, target)
        db.commit()
        for page in pages_to_ingest:
            enqueue_job(
                db,
                name="ingest.page",
                workload=JobWorkload.cpu,
                payload={"document_id": str(document_id), "page_number": page},
            )
        if not pages_to_ingest:
            refresh_rag_window_status(db, document_id)
    return {
        "document_id": str(document_id),
        "pages": target,
        "ingest_queued": pages_to_ingest,
    }


@eta(name="learn.transition_prep", workload=JobWorkload.cpu)
def transition_prep_job(payload: dict) -> dict:
    from app.services.question_pool import (
        INITIAL_BATCH_SIZE,
        count_assertions_on_page,
        enqueue_page_batch,
        enqueue_page_triage,
        get_page_coverage,
        get_question_budget,
        mark_transition_prep_done,
        selected_page_list,
    )

    document_id = UUID(payload["document_id"])
    current_page = int(payload["current_page"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}

        budget = get_question_budget(doc, current_page)
        generated = count_assertions_on_page(db, document_id, current_page)
        if generated < budget:
            remaining = budget - generated
            enqueue_page_batch(
                db,
                doc,
                page=current_page,
                batch_size=remaining,
                start_sequence=generated,
            )

        study = selected_page_list(doc)
        next_page: int | None = None
        if current_page in study:
            idx = study.index(current_page)
            if idx < len(study) - 1:
                next_page = study[idx + 1]

        if next_page is not None:
            if not get_page_coverage(doc, next_page):
                enqueue_page_triage(db, doc, page=next_page)
            elif count_assertions_on_page(db, document_id, next_page) == 0:
                next_budget = get_question_budget(doc, next_page)
                enqueue_page_batch(
                    db,
                    doc,
                    page=next_page,
                    batch_size=min(INITIAL_BATCH_SIZE, next_budget),
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


@eta(name="generate.questions", workload=JobWorkload.cpu)
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
