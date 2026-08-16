"""Newspaper catalog, channel knob, retention purge, and shared ingest entry."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import apply, choose, pick
from app.config import get_settings
from app.models import Document, JobWorkload
from app.repositories import newspaper as newspaper_repo
from app.services.document_purge import purge_document, purge_ingest_tmp
from app.services.parse import count_pdf_pages
from app.services.session_design import (
    NEWSPAPER_RETENTION_DAYS,
    evaluate_edition_practice_window,
    evaluate_newspaper_digest_enqueue,
    evaluate_newspaper_learn_complete_counts,
    plan_newspaper_edition_questions_limit,
    plan_newspaper_hub_heal,
    plan_newspaper_uncooked_followup,
)
from app.services.storage import _safe_storage_filename, ensure_bucket, put_bytes

logger = logging.getLogger(__name__)


def _save_newspaper_pdf(filename: str, data: bytes) -> str:
    ensure_bucket()
    key = f"newspaper/{uuid.uuid4()}/{_safe_storage_filename(filename)}"
    return put_bytes(key, data, "application/pdf", filename=filename)


def window_start(today: date | None = None) -> date:
    """Earliest edition_date learners may see (UTC date, last RETENTION_DAYS)."""
    today = today or datetime.now(timezone.utc).date()
    return newspaper_repo.retention_cutoff(
        datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
        days=NEWSPAPER_RETENTION_DAYS,
    )


def edition_in_practice_window(
    edition_date: date, *, today: date | None = None
) -> bool:
    """True when edition_date is within the practice retention window."""
    return evaluate_edition_practice_window(
        edition_date, cutoff=window_start(today)
    )


def list_catalog(db: Session) -> dict[str, Any]:
    since = window_start()
    papers = newspaper_repo.list_papers(db, since=since)
    return {
        "retention_days": NEWSPAPER_RETENTION_DAYS,
        "since": since.isoformat(),
        "papers": list(map(_catalog_paper, papers)),
    }


def _catalog_paper(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": p["paper_slug"],
        "title": p["paper_title"],
        "ready_days": p["ready_days"],
        "latest_date": pick(
            bool(p.get("latest_date")),
            lambda: p["latest_date"].isoformat(),
            lambda: None,
        ),
    }


def list_paper_days(
    db: Session,
    paper_slug: str,
    *,
    learner_key: str | None = None,
) -> dict[str, Any]:
    return pick(
        not newspaper_repo.is_brand_allowed(db, paper_slug),
        lambda: {
            "paper_slug": paper_slug,
            "paper_title": paper_slug,
            "since": window_start().isoformat(),
            "days": [],
        },
        lambda: _list_allowed_paper_days(db, paper_slug, learner_key=learner_key),
    )


def _list_allowed_paper_days(
    db: Session, paper_slug: str, *, learner_key: str | None
) -> dict[str, Any]:
    since = window_start()
    _heal_stuck_editions_for_hub(db, paper_slug, since=since)
    days = newspaper_repo.list_days_for_paper(db, paper_slug=paper_slug, since=since)
    title = pick(bool(days), lambda: days[0]["paper_title"], lambda: paper_slug)
    doc_ids = list(filter(None, (d.get("document_id") for d in days)))
    learner_progress: dict[uuid.UUID, dict[str, Any]] = {}
    question_totals: dict[str, int] = {}
    pick(
        bool(learner_key and doc_ids),
        lambda: _load_learner_day_stats(db, doc_ids, learner_key, learner_progress, question_totals),
        lambda: None,
    )
    day_items = [_day_item(d, paper_slug, learner_key, learner_progress, question_totals) for d in days]
    return {
        "paper_slug": paper_slug,
        "paper_title": title,
        "since": since.isoformat(),
        "days": day_items,
    }


def _load_learner_day_stats(
    db: Session,
    doc_ids: list[Any],
    learner_key: str,
    learner_progress: dict[uuid.UUID, dict[str, Any]],
    question_totals: dict[str, int],
) -> None:
    from app.services.document_learner_state import load_learner_progress_batch

    learner_progress.update(load_learner_progress_batch(db, doc_ids, learner_key))
    totals_rows = db.execute(
        text(
            """
            SELECT payload->>'artifact_id' AS document_id, COUNT(*)::int AS n
            FROM intel.assertion
            WHERE payload->>'artifact_id' = ANY(:doc_ids)
              AND status = 'active'
              AND COALESCE(payload->>'serve_mode', 'learn') = 'learn'
            GROUP BY 1
            """
        ),
        {"doc_ids": [str(doc_id) for doc_id in doc_ids]},
    ).mappings().all()
    question_totals.update({str(row["document_id"]): int(row["n"]) for row in totals_rows})


def _day_item(
    d: dict[str, Any],
    paper_slug: str,
    learner_key: str | None,
    learner_progress: dict[uuid.UUID, dict[str, Any]],
    question_totals: dict[str, int],
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": str(d["id"]),
        "edition_date": d["edition_date"].isoformat(),
        "status": d["status"],
        "document_id": pick(bool(d.get("document_id")), lambda: str(d["document_id"]), lambda: None),
        "has_blog": bool(d.get("blog_live")),
        "blog_href": pick(
            bool(d.get("blog_live")),
            lambda: f"/learn/newspaper/{paper_slug}/{d['edition_date'].isoformat()}",
            lambda: None,
        ),
    }
    pick(
        bool(learner_key and d.get("document_id")),
        lambda: item.__setitem__(
            "learner",
            _learner_day_state(d["document_id"], learner_progress, question_totals),
        ),
        lambda: None,
    )
    return item


def _learner_day_state(
    document_id: Any,
    learner_progress: dict[uuid.UUID, dict[str, Any]],
    question_totals: dict[str, int],
) -> dict[str, Any]:
    progress = learner_progress.get(document_id) or {}
    answered = progress.get("learn_answered_ids") or progress.get("answered_ids") or []
    answered_count = choose(isinstance(answered, list), len(answered), 0)
    total = question_totals.get(str(document_id), 0)
    flagged = bool(progress.get("learn_complete"))
    learn_complete = choose(
        flagged,
        True,
        evaluate_newspaper_learn_complete_counts(
            answered_count=answered_count,
            pool_count=total,
            generation_pending=False,
        ),
    )
    return {
        "questions_answered": answered_count,
        "questions_total": total,
        "learn_complete": learn_complete,
        "in_progress": answered_count > 0 and not learn_complete,
    }


def _heal_stuck_editions_for_hub(db: Session, paper_slug: str, *, since: date) -> None:
    """Recover stuck editions for the hub view, on-demand.

    Mirrors what ``newspaper.recover_stuck`` (the 15-min schedule) does, but
    scoped to the editions the learner is viewing so the catalog heals the
    instant a cook lands rather than up to 15 min later. Two orphan states:

    * doc still indexing/pending → re-queue missing RAG ingest (the schedule's
      ``_recover_stuck_editions`` path).
    * doc ready but edition still indexing → re-enqueue the page-1 cook, or
      promote if nothing is cookable (the schedule's
      ``_recover_editions_doc_ready_uncooked`` path).

    Failures are logged and swallowed — a healing error on one edition must never
    break the catalog list. The scheduler remains the authoritative backstop.
    """
    rows = db.execute(
        text(
            """
            SELECT e.id, e.document_id
            FROM qb.newspaper_edition e
            JOIN qb.documents d ON d.id = e.document_id
            WHERE e.paper_slug = :slug
              AND e.edition_date >= :since
              AND e.status IN ('indexing', 'pending')
            """
        ),
        {"slug": paper_slug, "since": since},
    ).mappings().all()

    for row in rows:
        _heal_one_hub_row(db, row)


def _heal_one_hub_row(db: Session, row: Any) -> None:
    document_id = row["document_id"]
    try:
        doc = db.get(Document, document_id)
        pick(doc is None, lambda: None, lambda: _heal_doc(db, doc))
    except Exception:
        logger.exception("hub heal failed for edition %s (doc %s)", row["id"], document_id)


def _heal_doc(db: Session, doc: Document) -> None:
    apply(
        plan_newspaper_hub_heal(doc_status=doc.status),
        {
            "reingest": lambda: _heal_reingest(db, doc),
            "recook": lambda: _heal_recook(db, doc),
            "idle": lambda: None,
        },
    )


def _heal_reingest(db: Session, doc: Document) -> None:
    from app.services.rag_window import maybe_recover_stuck_indexing

    maybe_recover_stuck_indexing(db, doc)


def _heal_recook(db: Session, doc: Document) -> None:
    from app.services.question_pool import page_range_bounds
    from app.services.question_pool_jobs import _enqueue_first_question_batch

    page_from, _ = page_range_bounds(doc)
    job = _enqueue_first_question_batch(db, doc, page=page_from)
    followup = plan_newspaper_uncooked_followup(cook_enqueued=job is not None)
    pick(followup == "promote_ready", lambda: maybe_mark_newspaper_edition_ready(db, doc), lambda: None)
    db.commit()


def get_channel(db: Session) -> dict[str, Any]:
    s = newspaper_repo.get_settings(db)
    return {
        "channel_ref": s.get("channel_ref") or "",
        "channel_label": s.get("channel_label") or "",
        "sync_cursor": s.get("sync_cursor"),
        "allowlist_only": bool(s.get("allowlist_only")),
        "updated_at": pick(
            bool(s.get("updated_at")),
            lambda: s["updated_at"].isoformat(),
            lambda: None,
        ),
    }


def update_channel(db: Session, *, channel_ref: str, channel_label: str = "") -> dict[str, Any]:
    pick(not channel_ref.strip(), lambda: _raise_value("channel_ref is required"), lambda: None)
    newspaper_repo.set_channel(db, channel_ref=channel_ref, channel_label=channel_label)
    return get_channel(db)


def _raise_value(msg: str) -> None:
    raise ValueError(msg)


def set_allowlist_only(db: Session, *, allowlist_only: bool) -> dict[str, Any]:
    newspaper_repo.set_allowlist_only(db, allowlist_only=allowlist_only)
    return get_channel(db)


def list_admin_brands(db: Session) -> dict[str, Any]:
    s = newspaper_repo.get_settings(db)
    brands = newspaper_repo.list_brands(db)
    return {
        "allowlist_only": bool(s.get("allowlist_only")),
        "brands": list(map(_admin_brand, brands)),
    }


def _admin_brand(b: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": b["paper_slug"],
        "title": b["paper_title"],
        "enabled": bool(b["enabled"]),
        "first_seen_at": pick(
            bool(b.get("first_seen_at")),
            lambda: b["first_seen_at"].isoformat(),
            lambda: None,
        ),
    }


def set_brand_enabled(db: Session, *, paper_slug: str, enabled: bool) -> dict[str, Any]:
    row = newspaper_repo.set_brand_enabled(db, paper_slug=paper_slug, enabled=enabled)
    pick(not row, lambda: _raise_value("Unknown paper — it appears after the channel posts it once"), lambda: None)
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
    return pick(
        not ensure_brand_and_allowed(db, paper_slug=paper_slug, paper_title=paper_title),
        lambda: _skip_allowlist(paper_slug),
        lambda: _create_edition_if_new(
            db,
            filename=filename,
            data=data,
            paper_slug=paper_slug,
            paper_title=paper_title,
            edition_date=edition_date,
            telegram_msg_id=telegram_msg_id,
            location_raw=location_raw,
        ),
    )


def _skip_allowlist(paper_slug: str) -> None:
    logger.info("skip paper %s — not on allowlist", paper_slug)
    return None


def _create_edition_if_new(
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
    existing = newspaper_repo.get_edition_by_paper_day(
        db, paper_slug=paper_slug, edition_date=edition_date
    )
    return pick(
        bool(existing),
        lambda: _skip_duplicate(paper_slug, edition_date, existing),
        lambda: _insert_edition_pdf(
            db,
            filename=filename,
            data=data,
            paper_slug=paper_slug,
            paper_title=paper_title,
            edition_date=edition_date,
            telegram_msg_id=telegram_msg_id,
            location_raw=location_raw,
        ),
    )


def _skip_duplicate(paper_slug: str, edition_date: date, existing: dict[str, Any]) -> None:
    logger.info(
        "skip duplicate newspaper edition %s %s (status=%s)",
        paper_slug,
        edition_date,
        existing.get("status"),
    )
    return None


def _insert_edition_pdf(
    db: Session,
    *,
    filename: str,
    data: bytes,
    paper_slug: str,
    paper_title: str,
    edition_date: date,
    telegram_msg_id: int | None,
    location_raw: str,
) -> uuid.UUID:
    settings = get_settings()
    pick(len(data) > settings.max_upload_bytes, lambda: _raise_value("PDF too large"), lambda: None)

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
    # Page-by-page ingest for every selected page (not rag_window — capped at
    # MAX_RAG_PAGES — and not ingest.document — loads the whole PDF + embed pass
    # in one job and OOMs CPU workers on ~20-page editions).
    doc.status = "indexing"
    doc.index_progress = 10
    from app.models import JobWorkload
    from app.services.jobs import batch_enqueue_jobs

    batch_enqueue_jobs(
        db,
        [
            {
                "name": "ingest.page",
                "workload": JobWorkload.cpu,
                "payload": {"document_id": str(doc.id), "page_number": page_num},
            }
            for page_num in range(1, page_count + 1)
        ],
    )
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
    return pick(not row, lambda: False, lambda: _purge_edition_row(db, row, hard_delete, delete_object))


def _purge_edition_row(db: Session, row: dict[str, Any], hard_delete: bool, delete_object: Any) -> bool:
    doc_id = row.get("document_id")
    pick(bool(doc_id), lambda: _purge_doc(db, doc_id, delete_object), lambda: None)
    pick(
        hard_delete,
        lambda: newspaper_repo.delete_edition_row(db, row["id"]),
        lambda: newspaper_repo.update_edition_status(db, row["id"], status="purged"),
    )
    db.commit()
    return True


def _purge_doc(db: Session, doc_id: Any, delete_object: Any) -> None:
    doc = db.get(Document, doc_id)
    pick(not doc, lambda: None, lambda: _purge_existing_doc(db, doc, doc_id, delete_object))


def _purge_existing_doc(db: Session, doc: Document, doc_id: Any, delete_object: Any) -> None:
    key = purge_document(db, doc)
    try:
        delete_object(key)
    except Exception:
        logger.debug("minio delete failed for %s", key, exc_info=True)
    purge_ingest_tmp(doc_id)


def purge_expired_editions(db: Session) -> int:
    from app.services.storage import delete_object

    cutoff = newspaper_repo.retention_cutoff(days=NEWSPAPER_RETENTION_DAYS)
    expired = newspaper_repo.list_expired_ready(db, before=cutoff)
    purged = 0
    for row in expired:
        _purge_expired_one(db, row, delete_object)
        purged += 1
    pick(bool(purged), lambda: db.commit(), lambda: None)
    return purged


def _purge_expired_one(db: Session, row: dict[str, Any], delete_object: Any) -> None:
    doc_id = row.get("document_id")
    pick(bool(doc_id), lambda: _purge_doc(db, doc_id, delete_object), lambda: None)
    newspaper_repo.update_edition_status(db, row["id"], status="purged")


def list_edition_questions(
    db: Session, edition_id: uuid.UUID, *, limit: int | None = None
) -> dict[str, Any]:
    ed = newspaper_repo.get_edition(db, edition_id)
    return pick(
        not ed or not edition_in_practice_window(ed["edition_date"]),
        lambda: {"edition": None, "items": []},
        lambda: _list_edition_questions_ready(db, ed, limit),
    )


def _list_edition_questions_ready(
    db: Session, ed: dict[str, Any], limit: int | None
) -> dict[str, Any]:
    doc_id = ed.get("document_id")
    return pick(
        not doc_id or ed.get("status") != "ready",
        lambda: {
            "edition": {
                "id": str(ed["id"]),
                "paper_slug": ed["paper_slug"],
                "paper_title": ed["paper_title"],
                "edition_date": ed["edition_date"].isoformat(),
                "status": ed["status"],
                "document_id": pick(bool(doc_id), lambda: str(doc_id), lambda: None),
            },
            "items": [],
        },
        lambda: _list_edition_items(db, ed, doc_id, limit),
    )


def _list_edition_items(
    db: Session, ed: dict[str, Any], doc_id: Any, limit: int | None
) -> dict[str, Any]:
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
        {"aid": str(doc_id), "limit": plan_newspaper_edition_questions_limit(limit)},
    ).mappings().all()
    return {
        "edition": {
            "id": str(ed["id"]),
            "paper_slug": ed["paper_slug"],
            "paper_title": ed["paper_title"],
            "edition_date": ed["edition_date"].isoformat(),
            "status": ed["status"],
            "document_id": str(doc_id),
        },
        "items": [_edition_question_item(r) for r in rows],
    }


def _edition_question_item(r: Any) -> dict[str, Any]:
    payload = choose(isinstance(r["payload"], dict), r["payload"], {})
    options = payload.get("options") or []
    options = pick(
        isinstance(options, dict),
        lambda: [options[k] for k in sorted(options.keys())],
        lambda: options,
    )
    return {
        "id": str(r["id"]),
        "question": payload.get("stem") or payload.get("question") or r.get("title") or "",
        "options": list(options),
        "is_multi": bool(payload.get("is_multi") or payload.get("multi")),
    }


def is_newspaper_document(doc: Document) -> bool:
    meta = doc.meta or {}
    return bool(meta.get("newspaper") or meta.get("hide_source") or meta.get("ingest_kind") == "newspaper")


def aggregate_worthy_newspaper_text(db: Session, document_id: uuid.UUID) -> str:
    """Edition digest source: pages the edition already cooks MCQs from.

    Learn MCQs only land on pages that cleared triage and the newspaper
    worthiness engine, so they are the edition's curated study set. Using raw
    PDF text let personal notices, local blotter, and masthead junk leak into
    Read analysis. Falls back to worthiness-filtered pages only when no MCQs
    exist yet (first digest enqueue right after page 1 cooks).
    """
    from app.repositories import seo as seo_repo
    from app.services.content_worthiness import evaluate_digest_page_worthy
    from app.services.question_pool import count_assertions_on_page
    from app.services.seo_gate import NewspaperDigestPage, plan_newspaper_digest

    page_rows = seo_repo.list_document_page_texts(db, document_id)
    pages: list[NewspaperDigestPage] = []
    for row in page_rows:
        _append_digest_page(db, document_id, row, pages, count_assertions_on_page, evaluate_digest_page_worthy, NewspaperDigestPage)
    return plan_newspaper_digest(pages)


def _append_digest_page(
    db: Session,
    document_id: uuid.UUID,
    row: Any,
    pages: list[Any],
    count_assertions_on_page: Any,
    evaluate_digest_page_worthy: Any,
    NewspaperDigestPage: Any,
) -> None:
    page = int(row["page_start"])
    page_text = (row.get("text") or "").strip()
    pick(
        not page_text,
        lambda: None,
        lambda: _push_digest_page(
            db,
            document_id,
            page,
            page_text,
            pages,
            count_assertions_on_page,
            evaluate_digest_page_worthy,
            NewspaperDigestPage,
        ),
    )


def _push_digest_page(
    db: Session,
    document_id: uuid.UUID,
    page: int,
    page_text: str,
    pages: list[Any],
    count_assertions_on_page: Any,
    evaluate_digest_page_worthy: Any,
    NewspaperDigestPage: Any,
) -> None:
    mcq_count = count_assertions_on_page(db, document_id, page, serve_mode="learn")
    pages.append(
        NewspaperDigestPage(
            page=page,
            text=page_text,
            mcq_count=mcq_count,
            worthy=evaluate_digest_page_worthy(mcq_count=mcq_count, page_text=page_text, db=db),
        )
    )


def newspaper_learn_ready(db: Session, doc: Document) -> bool:
    """True when Learn can open instantly (triage done + at least one MCQ on page 1).

    Newspaper editions stay ``indexing`` in the catalog until this passes so learners
    never see the upload-style "Writing your questions" cook screen on first open.
    """
    from app.services.question_pool import get_page_coverage, page_range_bounds
    from app.services.question_pool_jobs import count_assertions_on_page
    from app.services.session_design import evaluate_newspaper_catalog_ready

    return pick(
        not is_newspaper_document(doc),
        lambda: False,
        lambda: _newspaper_learn_ready_eval(
            db, doc, get_page_coverage, page_range_bounds, count_assertions_on_page, evaluate_newspaper_catalog_ready
        ),
    )


def _newspaper_learn_ready_eval(
    db: Session,
    doc: Document,
    get_page_coverage: Any,
    page_range_bounds: Any,
    count_assertions_on_page: Any,
    evaluate_newspaper_catalog_ready: Any,
) -> bool:
    page_from, _ = page_range_bounds(doc)
    cov = get_page_coverage(doc, page_from)
    budget = int((cov or {}).get("question_budget") or 0)
    return evaluate_newspaper_catalog_ready(
        is_newspaper=True,
        has_coverage=bool(cov),
        budget=budget,
        non_content=bool((cov or {}).get("non_content")),
        page1_mcq_count=pick(bool(cov), lambda: count_assertions_on_page(db, doc.id, page_from), lambda: 0),
    )


def mark_doc_ready_hook(db: Session, doc: Document) -> None:
    """Mark the catalog edition ready once newspaper_learn_ready passes."""
    pick(not is_newspaper_document(doc), lambda: None, lambda: _mark_edition_ready(db, doc))


def _mark_edition_ready(db: Session, doc: Document) -> None:
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
    pick(not newspaper_learn_ready(db, doc), lambda: None, lambda: _promote_edition_ready(db, doc))


def _promote_edition_ready(db: Session, doc: Document) -> None:
    mark_doc_ready_hook(db, doc)
    db.flush()
    _maybe_enqueue_edition_digest(db, doc)
    db.commit()


def _maybe_enqueue_edition_digest(db: Session, doc: Document) -> None:
    """Enqueue edition digest cook when edition becomes ready."""
    from app.repositories import seo as seo_repo
    from app.services.jobs import enqueue_job

    settings = seo_repo.get_settings(db)
    row = db.execute(
        text(
            """
            SELECT id, paper_slug, edition_date, blog_status, blog_post_id
            FROM qb.newspaper_edition
            WHERE document_id = :d AND status = 'ready'
            LIMIT 1
            """
        ),
        {"d": doc.id},
    ).mappings().first()
    source_key = pick(
        bool(row),
        lambda: f"{row['paper_slug']}:{row['edition_date'].isoformat()}",
        lambda: "",
    )
    action = evaluate_newspaper_digest_enqueue(
        cook_enabled=bool(settings.get("cook_enabled")),
        has_row=bool(row),
        already_published=bool(row)
        and row.get("blog_status") == "published"
        and bool(row.get("blog_post_id")),
        has_post_id=bool((row or {}).get("blog_post_id")),
        may_enqueue=pick(
            bool(row),
            lambda: seo_repo.edition_digest_may_enqueue(db, "newspaper_edition", source_key),
            lambda: False,
        ),
    )
    apply(
        action,
        {
            "skip_disabled": lambda: None,
            "skip_missing": lambda: None,
            "skip_published": lambda: None,
            "skip_has_post": lambda: None,
            "skip_attempted": lambda: None,
            "enqueue": lambda: _enqueue_digest(db, row, enqueue_job),
        },
    )


def _enqueue_digest(db: Session, row: Any, enqueue_job: Any) -> None:
    from app.repositories import newspaper as newspaper_repo

    newspaper_repo.reset_edition_blog_for_retry(db, row["id"])
    enqueue_job(
        db,
        name="seo.cook_edition_digest",
        workload=JobWorkload.io,
        payload={"edition_id": str(row["id"])},
    )
