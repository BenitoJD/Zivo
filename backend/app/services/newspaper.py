"""Newspaper catalog, channel knob, retention purge, and shared ingest entry."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document
from app.repositories import newspaper as newspaper_repo
from app.services.document_purge import purge_document, purge_ingest_tmp
from app.services.jobs import enqueue_rag_window
from app.services.parse import count_pdf_pages
from app.services.storage import _internal_client, _safe_storage_filename, ensure_bucket

logger = logging.getLogger(__name__)


def _save_newspaper_pdf(filename: str, data: bytes) -> str:
    ensure_bucket()
    key = f"newspaper/{uuid.uuid4()}/{_safe_storage_filename(filename)}"
    client = _internal_client()
    settings = get_settings()
    client.put_object(
        Bucket=settings.minio_bucket,
        Key=key,
        Body=data,
        ContentType="application/pdf",
    )
    return key


def window_start(today: date | None = None) -> date:
    """Earliest edition_date learners may see (UTC date, last RETENTION_DAYS)."""
    today = today or datetime.now(timezone.utc).date()
    return newspaper_repo.retention_cutoff(
        datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    )


def edition_in_practice_window(
    edition_date: date, *, today: date | None = None
) -> bool:
    """True when edition_date is within the practice retention window."""
    return edition_date >= window_start(today)


def list_catalog(db: Session) -> dict[str, Any]:
    since = window_start()
    papers = newspaper_repo.list_papers(db, since=since)
    return {
        "retention_days": newspaper_repo.RETENTION_DAYS,
        "since": since.isoformat(),
        "papers": [
            {
                "slug": p["paper_slug"],
                "title": p["paper_title"],
                "ready_days": p["ready_days"],
                "latest_date": p["latest_date"].isoformat() if p.get("latest_date") else None,
            }
            for p in papers
        ],
    }


def list_paper_days(db: Session, paper_slug: str) -> dict[str, Any]:
    if not newspaper_repo.is_brand_allowed(db, paper_slug):
        return {
            "paper_slug": paper_slug,
            "paper_title": paper_slug,
            "since": window_start().isoformat(),
            "days": [],
        }
    since = window_start()
    days = newspaper_repo.list_days_for_paper(db, paper_slug=paper_slug, since=since)
    title = days[0]["paper_title"] if days else paper_slug
    return {
        "paper_slug": paper_slug,
        "paper_title": title,
        "since": since.isoformat(),
        "days": [
            {
                "id": str(d["id"]),
                "edition_date": d["edition_date"].isoformat(),
                "status": d["status"],
                "document_id": str(d["document_id"]) if d.get("document_id") else None,
            }
            for d in days
        ],
    }


def get_channel(db: Session) -> dict[str, Any]:
    s = newspaper_repo.get_settings(db)
    return {
        "channel_ref": s.get("channel_ref") or "",
        "channel_label": s.get("channel_label") or "",
        "sync_cursor": s.get("sync_cursor"),
        "allowlist_only": bool(s.get("allowlist_only")),
        "updated_at": s["updated_at"].isoformat() if s.get("updated_at") else None,
    }


def update_channel(db: Session, *, channel_ref: str, channel_label: str = "") -> dict[str, Any]:
    if not channel_ref.strip():
        raise ValueError("channel_ref is required")
    newspaper_repo.set_channel(db, channel_ref=channel_ref, channel_label=channel_label)
    return get_channel(db)


def set_allowlist_only(db: Session, *, allowlist_only: bool) -> dict[str, Any]:
    newspaper_repo.set_allowlist_only(db, allowlist_only=allowlist_only)
    return get_channel(db)


def list_admin_brands(db: Session) -> dict[str, Any]:
    s = newspaper_repo.get_settings(db)
    brands = newspaper_repo.list_brands(db)
    return {
        "allowlist_only": bool(s.get("allowlist_only")),
        "brands": [
            {
                "slug": b["paper_slug"],
                "title": b["paper_title"],
                "enabled": bool(b["enabled"]),
                "first_seen_at": b["first_seen_at"].isoformat() if b.get("first_seen_at") else None,
            }
            for b in brands
        ],
    }


def set_brand_enabled(db: Session, *, paper_slug: str, enabled: bool) -> dict[str, Any]:
    row = newspaper_repo.set_brand_enabled(db, paper_slug=paper_slug, enabled=enabled)
    if not row:
        raise ValueError("Unknown paper — it appears after the channel posts it once")
    return {
        "slug": row["paper_slug"],
        "title": row["paper_title"],
        "enabled": bool(row["enabled"]),
    }


def ensure_brand_and_allowed(db: Session, *, paper_slug: str, paper_title: str) -> bool:
    """Register brand if new; return True if ingest should proceed."""
    newspaper_repo.upsert_brand(
        db, paper_slug=paper_slug, paper_title=paper_title, enabled_if_new=False
    )
    db.commit()
    return newspaper_repo.is_brand_allowed(db, paper_slug)


def create_edition_from_pdf(
    db: Session,
    *,
    filename: str,
    data: bytes,
    paper_slug: str,
    paper_title: str,
    edition_date: date,
    telegram_msg_id: int | None,
    location_raw: str,
) -> uuid.UUID | None:
    """Idempotent: if (paper, day) exists, skip. Returns edition id or None if skipped."""
    if not ensure_brand_and_allowed(db, paper_slug=paper_slug, paper_title=paper_title):
        logger.info("skip paper %s — not on allowlist", paper_slug)
        return None

    existing = newspaper_repo.get_edition_by_paper_day(
        db, paper_slug=paper_slug, edition_date=edition_date
    )
    if existing:
        logger.info(
            "skip duplicate newspaper edition %s %s (status=%s)",
            paper_slug,
            edition_date,
            existing.get("status"),
        )
        return None

    settings = get_settings()
    if len(data) > settings.max_upload_bytes:
        raise ValueError("PDF too large")

    storage_key = _save_newspaper_pdf(filename, data)
    page_count = count_pdf_pages(data) or 1
    meta = {
        "is_public": True,
        "newspaper": True,
        "hide_source": True,
        "paper_slug": paper_slug,
        "paper_title": paper_title,
        "edition_date": edition_date.isoformat(),
        "location_raw": location_raw or "",
        "page_count": page_count,
        "ingest_kind": "newspaper",
    }
    doc = Document(
        account_id=None,
        slug=f"newspaper-{paper_slug}-{edition_date.isoformat()}"[:64],
        filename=filename[:512],
        content_type="application/pdf",
        size_bytes=len(data),
        storage_key=storage_key,
        status="pending",
        meta=meta,
    )
    db.add(doc)
    db.flush()

    edition_id = newspaper_repo.insert_edition(
        db,
        paper_slug=paper_slug,
        paper_title=paper_title,
        edition_date=edition_date,
        document_id=doc.id,
        telegram_msg_id=telegram_msg_id,
        location_raw=location_raw or "",
        status="indexing",
    )

    selected = {"from": 1, "to": page_count, "pages": list(range(1, page_count + 1))}
    # Import via question_pool facade (not question_pool_jobs) to avoid circular
    # import when jobs module loads pool mid-init.
    from app.services.question_pool import reset_for_new_page_range

    reset_for_new_page_range(db, doc, selected)
    newspaper_repo.update_edition_status(db, edition_id, status="indexing", document_id=doc.id)
    enqueue_rag_window(db, doc.id, current_page=1)
    db.commit()
    logger.info(
        "newspaper edition created id=%s paper=%s date=%s doc=%s pages=%s",
        edition_id,
        paper_slug,
        edition_date,
        doc.id,
        page_count,
    )
    return edition_id


def purge_edition(db: Session, edition_id: uuid.UUID, *, hard_delete: bool = False) -> bool:
    """Purge one edition's document (+ MinIO). hard_delete removes the row for re-ingest."""
    from app.services.storage import delete_object

    row = newspaper_repo.get_edition(db, edition_id)
    if not row:
        return False
    doc_id = row.get("document_id")
    if doc_id:
        doc = db.get(Document, doc_id)
        if doc:
            key = purge_document(db, doc)
            try:
                delete_object(key)
            except Exception:
                logger.debug("minio delete failed for %s", key, exc_info=True)
            purge_ingest_tmp(doc_id)
    if hard_delete:
        newspaper_repo.delete_edition_row(db, edition_id)
    else:
        newspaper_repo.update_edition_status(db, edition_id, status="purged")
    db.commit()
    return True


def purge_expired_editions(db: Session) -> int:
    from app.services.storage import delete_object

    cutoff = newspaper_repo.retention_cutoff()
    expired = newspaper_repo.list_expired_ready(db, before=cutoff)
    purged = 0
    for row in expired:
        doc_id = row.get("document_id")
        if doc_id:
            doc = db.get(Document, doc_id)
            if doc:
                key = purge_document(db, doc)
                try:
                    delete_object(key)
                except Exception:
                    logger.debug("minio delete failed for %s", key, exc_info=True)
                purge_ingest_tmp(doc_id)
        newspaper_repo.update_edition_status(db, row["id"], status="purged")
        purged += 1
    if purged:
        db.commit()
    return purged


def list_edition_questions(db: Session, edition_id: uuid.UUID, *, limit: int = 40) -> dict[str, Any]:
    ed = newspaper_repo.get_edition(db, edition_id)
    if not ed:
        return {"edition": None, "items": []}
    if not edition_in_practice_window(ed["edition_date"]):
        return {"edition": None, "items": []}
    doc_id = ed.get("document_id")
    if not doc_id or ed.get("status") != "ready":
        return {
            "edition": {
                "id": str(ed["id"]),
                "paper_slug": ed["paper_slug"],
                "paper_title": ed["paper_title"],
                "edition_date": ed["edition_date"].isoformat(),
                "status": ed["status"],
                "document_id": str(doc_id) if doc_id else None,
            },
            "items": [],
        }

    rows = db.execute(
        text(
            """
            SELECT a.id, a.payload
            FROM intel.assertion a
            JOIN intel.concept c ON c.id = a.type_concept_id
            WHERE a.payload->>'artifact_id' = :aid
              AND a.status = 'active'
              AND c.uri = '/vocab/assertion/question.mcq'
            ORDER BY a.recorded_at ASC
            LIMIT :limit
            """
        ),
        {"aid": str(doc_id), "limit": limit},
    ).mappings().all()

    items = []
    for r in rows:
        payload = r["payload"] if isinstance(r["payload"], dict) else {}
        options = payload.get("options") or []
        if isinstance(options, dict):
            options = [options[k] for k in sorted(options.keys())]
        items.append(
            {
                "id": str(r["id"]),
                "question": payload.get("stem") or payload.get("question") or r.get("title") or "",
                "options": list(options),
                "is_multi": bool(payload.get("is_multi") or payload.get("multi")),
            }
        )

    return {
        "edition": {
            "id": str(ed["id"]),
            "paper_slug": ed["paper_slug"],
            "paper_title": ed["paper_title"],
            "edition_date": ed["edition_date"].isoformat(),
            "status": ed["status"],
            "document_id": str(doc_id),
        },
        "items": items,
    }


def is_newspaper_document(doc: Document) -> bool:
    meta = doc.meta or {}
    return bool(meta.get("newspaper") or meta.get("hide_source") or meta.get("ingest_kind") == "newspaper")


def newspaper_learn_ready(db: Session, doc: Document) -> bool:
    """True when Learn can open instantly (triage done + at least one MCQ on page 1).

    Newspaper editions stay ``indexing`` in the catalog until this passes so learners
    never see the upload-style "Writing your questions" cook screen on first open.
    """
    if not is_newspaper_document(doc):
        return False
    from app.services.question_pool import get_page_coverage, page_range_bounds
    from app.services.question_pool_jobs import count_assertions_on_page

    page_from, _ = page_range_bounds(doc)
    cov = get_page_coverage(doc, page_from)
    if not cov:
        return False
    budget = int(cov.get("question_budget") or 0)
    if budget <= 0 or cov.get("non_content"):
        return True
    return count_assertions_on_page(db, doc.id, page_from) >= 1


def mark_doc_ready_hook(db: Session, doc: Document) -> None:
    """Mark the catalog edition ready once newspaper_learn_ready passes."""
    if not is_newspaper_document(doc):
        return
    db.execute(
        text(
            """
            UPDATE qb.newspaper_edition
            SET status = 'ready', updated_at = now()
            WHERE document_id = :d AND status <> 'purged'
            """
        ),
        {"d": doc.id},
    )


def maybe_mark_newspaper_edition_ready(db: Session, doc: Document) -> None:
    """Promote edition to catalog-ready after background cook lands the first MCQ."""
    if not newspaper_learn_ready(db, doc):
        return
    mark_doc_ready_hook(db, doc)
    db.commit()
