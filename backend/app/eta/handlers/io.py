"""IO workload handlers."""

import logging
from uuid import UUID

from app.db import SessionLocal
from app.eta.registry import eta
from app.models import Document, JobWorkload
from app.services.chunks import persist_document_index
from app.services.jobs import enqueue_job
from app.services.storage import delete_object, get_json, ingest_tmp_key


logger = logging.getLogger(__name__)

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
            logger.debug("ingest temp-object cleanup failed", exc_info=True)
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
    from app.services.source_fingerprint import mark_artifact_fresh

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        summary = asyncio.run(generate_whole_doc_summary(db, document_id))
        doc = db.get(Document, document_id)
        if doc:
            meta = dict(doc.meta or {})
            meta["summary"] = summary
            doc.meta = meta
            mark_artifact_fresh(db, document_id, "summary")
            db.commit()
    return {"document_id": str(document_id), "summary": summary}


@eta(name="topics.generate", workload=JobWorkload.io)
def topics_generate(payload: dict) -> dict:
    """Build the Explain topic outline for a document (off the answer path)."""
    from app.services.topics import run_topics_generation

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        topics = run_topics_generation(db, document_id)
    return {"document_id": str(document_id), "topic_count": len(topics)}


@eta(name="explain.generate", workload=JobWorkload.io)
def explain_generate(payload: dict) -> dict:
    """Build one plain-language topic explanation (off the answer path)."""
    from app.services.topics import run_explanation

    document_id = UUID(payload["document_id"])
    topic_key = payload["topic_key"]
    with SessionLocal() as db:
        explanation = run_explanation(
            db,
            document_id,
            topic_key,
            title=payload.get("title", ""),
            summary=payload.get("summary", ""),
        )
    return {"document_id": str(document_id), "topic_key": topic_key, "chars": len(explanation)}


@eta(name="notes.generate", workload=JobWorkload.io)
def notes_generate(payload: dict) -> dict:
    """Build structured study notes (or a cheat sheet) for a document."""
    from app.services.study_artifacts import run_notes_generation

    document_id = UUID(payload["document_id"])
    kind = payload.get("kind", "notes")
    with SessionLocal() as db:
        content = run_notes_generation(db, document_id, kind)
    return {"document_id": str(document_id), "kind": kind, "chars": len(content)}


@eta(name="flashcards.generate", workload=JobWorkload.io)
def flashcards_generate(payload: dict) -> dict:
    """Build the active-recall flashcard deck for a document."""
    from app.services.study_artifacts import run_flashcards_generation

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        cards = run_flashcards_generation(db, document_id)
    return {"document_id": str(document_id), "card_count": len(cards)}


@eta(name="memory_palace.generate", workload=JobWorkload.io)
def memory_palace_generate(payload: dict) -> dict:
    """Build the memory-palace journey for a document (off the answer path)."""
    from app.services.memory_palace import run_palace_generation

    document_id = UUID(payload["document_id"])
    setting = payload.get("setting", "")
    with SessionLocal() as db:
        palace = run_palace_generation(db, document_id, setting)
    return {"document_id": str(document_id), "stations": len((palace or {}).get("stations", []))}


@eta(name="quiz.generate", workload=JobWorkload.io)
def quiz_generate(payload: dict) -> dict:
    """Build a quiz/worksheet for a document (Question Generator, off the answer path)."""
    from app.services.quiz import run_quiz_generation

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        questions = run_quiz_generation(
            db,
            document_id,
            types=payload.get("types", ["mcq"]),
            count=payload.get("count", 10),
            difficulty=payload.get("difficulty", "mixed"),
        )
    return {"document_id": str(document_id), "questions": len(questions)}


@eta(name="mains.generate", workload=JobWorkload.io)
def mains_generate(payload: dict) -> dict:
    """Generate a Mains descriptive question + hidden marking scheme for a document."""
    from app.services.mains import run_mains_generation

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        run_mains_generation(db, document_id)
    return {"document_id": str(document_id)}


@eta(name="mains.grade", workload=JobWorkload.io)
def mains_grade(payload: dict) -> dict:
    """Grade a submitted Mains answer — vision-LLM OCR for photos, then examiner scoring."""
    from app.services.mains import run_mains_grading

    document_id = UUID(payload["document_id"])
    with SessionLocal() as db:
        run_mains_grading(db, document_id)
    return {"document_id": str(document_id)}


@eta(name="coach.mcq_page", workload=JobWorkload.io)
def coach_mcq_page(payload: dict) -> dict:
    """Precompute per-option feedback for a page's MCQs (off the answer path).

    Makes grading an instant lookup for even the first learner. Idempotent — only
    coaches assertions that don't already have option_feedback.
    """
    from app.services.option_feedback import coach_page_assertions

    document_id = UUID(payload["document_id"])
    page_number = int(payload["page_number"])
    with SessionLocal() as db:
        coached = coach_page_assertions(db, document_id=document_id, page_number=page_number)
    return {"document_id": str(document_id), "page_number": page_number, "coached": coached}
