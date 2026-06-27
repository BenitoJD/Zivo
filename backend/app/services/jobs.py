import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.eta.submit import build_job
from app.models import Job, JobWorkload

_ETA_NOTIFY_CHANNEL = "zivo_eta_job"


def _wake_workers(db: Session) -> None:
    try:
        db.execute(text("SELECT pg_notify(:channel, '')"), {"channel": _ETA_NOTIFY_CHANNEL})
    except Exception:
        pass
    try:
        from app.eta.job_notify import wake_eta_workers

        wake_eta_workers()
    except Exception:
        pass


def batch_enqueue_jobs(
    db: Session,
    specs: list[dict],
    *,
    chunk_size: int = 50,
) -> list[Job]:
    """Enqueue many jobs with batched commits and one NOTIFY per chunk."""
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
