"""CPU workload handlers."""

from uuid import UUID

from app.db import SessionLocal
from app.eta.registry import eta
from app.models import Document, JobWorkload
from app.services.chunking import chunk_pages
from app.services.chunks import finalize_image_document, persist_document_index
from app.services.embed import embed_texts
from app.services.jobs import enqueue_job
from app.services.parse import parse_document
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
