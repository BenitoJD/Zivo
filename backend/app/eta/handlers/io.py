"""IO workload handlers."""

from uuid import UUID

from app.db import SessionLocal
from app.eta.registry import eta
from app.models import Document, JobWorkload
from app.services.chunks import persist_document_index
from app.services.jobs import enqueue_job
from app.services.storage import delete_object, get_json, ingest_tmp_key


@eta(name="generate.questions", workload=JobWorkload.io)
def generate_questions_job(payload: dict) -> dict:
    """Question generation is LLM-I/O-bound (multi-second HTTP calls), not CPU.

    This is a SYNC handler. The async IO worker dispatches sync handlers to a
    worker thread via asyncio.to_thread (worker_async._process_job), so each of
    the CONCURRENCY=16 slots runs in its own OS thread. That lets up to 16
    generations overlap on the multi-second LLM HTTP calls without any one
    blocking the others — and crucially, without blocking the event loop that
    drives the reservation loop and other handlers.

    run_generation is async (it awaits litellm.acompletion). We drive it with
    asyncio.run, which spins a fresh per-call loop. This is correct here
    because the LLM call dominates latency by orders of magnitude — the loop
    create/teardown cost (~microseconds) is negligible against the multi-second
    completion, and a fresh loop avoids sharing state across concurrent threads.
    """
    import asyncio

    from app.graphs.generation_graph import run_generation
    from app.services.question_pool import on_batch_failed

    document_id = UUID(payload["document_id"])
    page_number = int(payload.get("page_number") or 0)
    with SessionLocal() as db:
        try:
            return asyncio.run(run_generation(db, document_id, payload))
        except Exception:
            on_batch_failed(db, document_id, page=page_number)
            raise


@eta(name="ingest.fetch_file", workload=JobWorkload.io)
def fetch_file(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}
        doc.status = "indexing"
        doc.index_progress = 10
        db.commit()

        enqueue_job(
            db,
            name="ingest.document",
            workload=JobWorkload.cpu,
            payload={"document_id": str(document_id)},
        )
    return {"document_id": str(document_id)}


@eta(name="ingest.write_chunks", workload=JobWorkload.io)
def write_chunks(payload: dict) -> dict:
    """Retry path — happy-path ingest persists chunks in ingest.embed_chunks."""
    document_id = UUID(payload["document_id"])
    key = ingest_tmp_key(document_id, "embedded")
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return {"skipped": True}
        data = get_json(key)
        chunks = data.get("chunks", [])
        embeddings = data.get("embeddings", [])
        count = persist_document_index(db, document_id, chunks, embeddings)
        try:
            delete_object(key)
            delete_object(ingest_tmp_key(document_id, "pages"))
            delete_object(ingest_tmp_key(document_id, "chunks"))
        except Exception:
            pass
    return {"document_id": str(document_id), "chunks": count}


@eta(name="summarize.start", workload=JobWorkload.io)
def summarize_start(payload: dict) -> dict:
    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        enqueue_job(
            db,
            name="summarize.generate",
            workload=JobWorkload.io,
            payload=payload,
        )
    return {"document_id": str(document_id), "status": "queued"}


@eta(name="summarize.generate", workload=JobWorkload.io)
def summarize_generate(payload: dict) -> dict:
    import asyncio

    from app.graphs.summarize_graph import generate_whole_doc_summary

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        summary = asyncio.run(generate_whole_doc_summary(db, document_id))
        doc = db.get(Document, document_id)
        if doc:
            meta = dict(doc.meta or {})
            meta["summary"] = summary
            doc.meta = meta
            db.commit()
    return {"document_id": str(document_id), "summary": summary}
