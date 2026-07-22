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


def batch_enqueue_jobs(
    db: Session,
    specs: list[dict],
    *,
    chunk_size: int = 50,
) -> list[Job]:
    """Enqueue many jobs with batched commits and one NOTIFY per chunk."""
    from app.eta.submit import build_job

    jobs: list[Job] = []
    for offset in range(0, len(specs), chunk_size):
        chunk = specs[offset : offset + chunk_size]
        batch: list[Job] = []
        for spec in chunk:
            job = build_job(**spec)
            db.add(job)
            batch.append(job)
        db.commit()
        _wake_workers(db)
        for job in batch:
            db.refresh(job)
        jobs.extend(batch)
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
