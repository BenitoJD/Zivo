import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Job, JobWorkload

# NOTE: `build_job` (app.eta.submit) is imported lazily inside the enqueue
# functions below — importing it at module load pulls in the eta registry, which
# imports the handlers (app.eta.handlers.cpu), which import this module back,
# forming a cycle. Deferring it (the same pattern _wake_workers uses for
# job_notify) lets this service import cleanly on its own.

_ETA_NOTIFY_CHANNEL = "zivo_eta_job"


logger = logging.getLogger(__name__)

def _wake_workers(db: Session) -> None:
    try:
        db.execute(text("SELECT pg_notify(:channel, '')"), {"channel": _ETA_NOTIFY_CHANNEL})
    except Exception:
        logger.debug("pg_notify worker wake failed", exc_info=True)
    try:
        from app.eta.job_notify import wake_eta_workers

        wake_eta_workers()
    except Exception:
        logger.debug("eta worker wake failed", exc_info=True)


def _payload_key(name: str, payload: dict | None) -> str:
    """Identity of a unit of work: same name + same payload == same job."""
    return name + "\x1f" + json.dumps(payload or {}, sort_keys=True, default=str)


def _dedupable(spec: dict) -> bool:
    """Only standalone work is deduped — a DAG node's identity includes its
    edges, so two same-payload jobs under different parents are NOT the same job.
    """
    return not spec.get("execution_id") and not spec.get("parent_job_ids")


def _already_queued_keys(db: Session, specs: list[dict]) -> set[str]:
    """Identity keys of standalone jobs already sitting in ``queued`` for these
    documents.

    Scoped by document_id so the lookup rides ix_jobs_payload_document_id; specs
    without a document_id are not deduped (nothing cheap to scope by).
    """
    doc_ids = {
        str(d)
        for s in specs
        if _dedupable(s) and (d := (s.get("payload") or {}).get("document_id"))
    }
    if not doc_ids:
        return set()
    rows = db.execute(
        text(
            """
            SELECT name, payload FROM qb.jobs
            WHERE status = 'queued'
              AND execution_id IS NULL
              AND payload->>'document_id' = ANY(CAST(:doc_ids AS text[]))
            """
        ),
        {"doc_ids": list(doc_ids)},
    ).all()
    return {_payload_key(name, payload) for name, payload in rows}


def batch_enqueue_jobs(
    db: Session,
    specs: list[dict],
    *,
    chunk_size: int = 50,
) -> list[Job]:
    """Enqueue many jobs with batched commits and one NOTIFY per chunk.

    Identical work already queued is skipped. The recovery schedulers re-enqueue
    whenever they see no *live* job, and ACTIVE_JOB_LIVENESS_SQL calls a queued
    job stale after ETA_STALE_QUEUED_TIMEOUT — so a backed-up queue used to
    breed a fresh duplicate set every scheduler tick, forever (2026-08-09: 1,709
    copies of the same 8 documents' page ingests, which starved the real work and
    left newspaper editions "Preparing" for days). A queued job always runs
    eventually; a second copy of it is pure waste.
    """
    from app.eta.submit import build_job

    seen = _already_queued_keys(db, specs)
    jobs: list[Job] = []
    skipped = 0
    for offset in range(0, len(specs), chunk_size):
        chunk = specs[offset : offset + chunk_size]
        batch: list[Job] = []
        for spec in chunk:
            key = _payload_key(spec.get("name", ""), spec.get("payload"))
            if _dedupable(spec):
                if key in seen:
                    skipped += 1
                    continue
                seen.add(key)  # also dedups repeats within this call
            job = build_job(**spec)
            db.add(job)
            batch.append(job)
        if not batch:
            continue
        db.commit()
        _wake_workers(db)
        for job in batch:
            db.refresh(job)
        jobs.extend(batch)
    if skipped:
        logger.info("batch_enqueue_jobs skipped %s already-queued duplicate job(s)", skipped)
    return jobs


def enqueue_job(
    db: Session,
    *,
    name: str,
    workload: JobWorkload,
    payload: dict,
    account_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
    parent_job_ids: list[str] | None = None,
) -> Job:
    """Enqueue a single ETA job, routed through the registry-aware builder.

    Workload/priority are resolved from the handler registry when the job name is
    registered; explicit ``workload`` overrides only when the handler is unknown.
    """
    from app.eta.submit import build_job

    spec = {
        "name": name,
        "payload": payload,
        "execution_id": execution_id,
        "parent_job_ids": parent_job_ids,
    }
    if _dedupable(spec):
        key = _payload_key(name, payload)
        existing_id = next(
            (
                row_id
                for row_id, row_name, row_payload in db.execute(
                    text(
                        """
                        SELECT id, name, payload FROM qb.jobs
                        WHERE status = 'queued'
                          AND execution_id IS NULL
                          AND name = :name
                          AND payload->>'document_id' = :doc_id
                        """
                    ),
                    {"name": name, "doc_id": str((payload or {}).get("document_id"))},
                ).all()
                if _payload_key(row_name, row_payload) == key
            ),
            None,
        )
        if existing_id is not None:
            # Identical work is already waiting — hand back that job rather than
            # breeding a duplicate every scheduler tick. See batch_enqueue_jobs.
            logger.info("enqueue_job reused already-queued %s for the same payload", name)
            return db.get(Job, existing_id)

    job = build_job(
        name=name,
        payload=payload,
        account_id=account_id,
        workload=workload,
        execution_id=execution_id,
        parent_job_ids=parent_job_ids,
    )
    db.add(job)
    db.commit()
    _wake_workers(db)
    db.refresh(job)
    return job


def enqueue_ingest(
    db: Session,
    document_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None = None,
    page_range: dict | None = None,
    activity_id: uuid.UUID | None = None,
) -> Job:
    payload: dict = {"document_id": str(document_id)}
    if page_range:
        payload["page_range"] = page_range
    if activity_id:
        payload["activity_id"] = str(activity_id)
    job = enqueue_job(
        db,
        name="ingest.fetch_file",
        workload=JobWorkload.io,
        payload=payload,
        account_id=account_id,
    )
    if activity_id:
        job.activity_id = activity_id
    return job


def enqueue_generate(
    db: Session,
    *,
    document_id: uuid.UUID,
    account_id: uuid.UUID | None,
    activity_id: uuid.UUID,
    options: dict,
) -> Job:
    payload = {
        "document_id": str(document_id),
        "activity_id": str(activity_id),
        **options,
    }
    job = enqueue_job(
        db,
        name="generate.questions",
        workload=JobWorkload.cpu,
        payload=payload,
        account_id=account_id,
    )
    job.activity_id = activity_id
    return job


def enqueue_rag_window(
    db: Session,
    document_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None = None,
    current_page: int | None = None,
) -> Job:
    payload: dict = {"document_id": str(document_id)}
    if current_page is not None:
        payload["current_page"] = int(current_page)
    return enqueue_job(
        db,
        name="ingest.rag_window",
        workload=JobWorkload.cpu,
        payload=payload,
        account_id=account_id,
    )


def enqueue_full_range_ingest(
    db: Session,
    document_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None = None,
    current_page: int | None = None,
) -> Job:
    payload: dict = {"document_id": str(document_id)}
    if current_page is not None:
        payload["current_page"] = int(current_page)
    return enqueue_job(
        db,
        name="ingest.full_range",
        workload=JobWorkload.cpu,
        payload=payload,
        account_id=account_id,
    )


def enqueue_transition_prep(
    db: Session,
    document_id: uuid.UUID,
    *,
    current_page: int,
    account_id: uuid.UUID | None = None,
) -> Job:
    return enqueue_job(
        db,
        name="learn.transition_prep",
        workload=JobWorkload.cpu,
        payload={"document_id": str(document_id), "current_page": int(current_page)},
        account_id=account_id,
    )


def enqueue_summarize(db: Session, document_id: uuid.UUID, account_id: uuid.UUID) -> Job:
    return enqueue_job(
        db,
        name="summarize.start",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id), "account_id": str(account_id)},
        account_id=account_id,
    )


def enqueue_topics(db: Session, document_id: uuid.UUID) -> Job:
    """Background generation of the Explain topic outline (works for guest docs too)."""
    return enqueue_job(
        db,
        name="topics.generate",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id)},
    )


def enqueue_explanation(
    db: Session, document_id: uuid.UUID, topic_key: str, *, title: str, summary: str
) -> Job:
    """Background generation of one plain-language topic explanation (off the answer path)."""
    return enqueue_job(
        db,
        name="explain.generate",
        workload=JobWorkload.io,
        payload={
            "document_id": str(document_id),
            "topic_key": topic_key,
            "title": title,
            "summary": summary,
        },
    )


def enqueue_notes(db: Session, document_id: uuid.UUID, kind: str) -> Job:
    """Background generation of structured study notes / a cheat sheet."""
    return enqueue_job(
        db,
        name="notes.generate",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id), "kind": kind},
    )


def enqueue_flashcards(db: Session, document_id: uuid.UUID) -> Job:
    """Background generation of the active-recall flashcard deck."""
    return enqueue_job(
        db,
        name="flashcards.generate",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id)},
    )


def enqueue_memory_palace(db: Session, document_id: uuid.UUID, setting: str = "") -> Job:
    """Background generation of the memory-palace journey (off the answer path)."""
    return enqueue_job(
        db,
        name="memory_palace.generate",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id), "setting": setting},
    )


def enqueue_quiz(
    db: Session, document_id: uuid.UUID, *, types: list[str], count: int, difficulty: str
) -> Job:
    """Background generation of a quiz/worksheet (Question Generator, off the answer path)."""
    return enqueue_job(
        db,
        name="quiz.generate",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id), "types": types, "count": count, "difficulty": difficulty},
    )


def enqueue_coach_page(
    db: Session, *, document_id: uuid.UUID, page: int, account_id: uuid.UUID | None = None
) -> Job:
    """Precompute per-option grade feedback for a page's MCQs (off the answer path)."""
    return enqueue_job(
        db,
        name="coach.mcq_page",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id), "page_number": int(page)},
        account_id=account_id,
    )


def enqueue_mains_generate(db: Session, document_id: uuid.UUID) -> Job:
    """Generate a Mains descriptive question + hidden marking scheme (off the request path)."""
    return enqueue_job(
        db,
        name="mains.generate",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id)},
    )


def enqueue_mains_grade(db: Session, document_id: uuid.UUID) -> Job:
    """Grade a submitted Mains answer (vision-LLM OCR for photos + examiner scoring)."""
    return enqueue_job(
        db,
        name="mains.grade",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id)},
    )


def enqueue_offline_pack(db: Session, pack_id: uuid.UUID) -> Job:
    """Build an offline study pack off the request path (ADR 0006)."""
    return enqueue_job(
        db,
        name="offline.build_pack",
        workload=JobWorkload.io,
        payload={"pack_id": str(pack_id)},
    )
