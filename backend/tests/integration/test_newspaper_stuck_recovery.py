"""Recovery for newspaper editions stranded "document ready, edition indexing".

Regression for the orphan state where RAG indexing finished (document=ready)
but the MCQ cook died / exhausted retries, leaving the edition stuck at
``indexing`` (catalog "Preparing") with no active ``generate.questions`` job and
no scheduler to re-enqueue one. See ``_recover_editions_doc_ready_uncooked``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import Document

GUEST_ID = "e" * 32


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def _seed_stuck_edition() -> tuple[str, str]:
    """Seed a ready document whose newspaper edition is stuck at 'indexing'."""
    doc_id = uuid.uuid4()
    edition_id = uuid.uuid4()
    db = SessionLocal()
    try:
        doc = Document(
            id=doc_id,
            slug="stuck-edition",
            filename="the-hindu.pdf",
            content_type="application/pdf",
            size_bytes=128,
            storage_key=f"test/{doc_id}.pdf",
            status="ready",  # document finished indexing...
            meta={
                "guest_id": GUEST_ID,
                "page_count": 6,
                "newspaper": True,
                "paper_slug": "the-hindu",
                "edition_date": "2026-07-31",
                "selected_range": {"from": 1, "to": 6, "pages": [1, 2, 3, 4, 5, 6]},
                "question_pool_initialized": True,  # the original cook set this then died
                "question_progress": {
                    "current_page": 1,
                    "answered_ids": [],
                    "answered_on_page": 0,
                    "generated_on_page": 0,
                    "generation_pending": False,
                },
            },
        )
        db.add(doc)
        db.commit()
        # ...but the edition is still indexing (the cook never produced an MCQ).
        old = datetime.now(timezone.utc) - timedelta(minutes=40)
        db.execute(
            text(
                """
                INSERT INTO qb.newspaper_edition
                  (id, document_id, paper_slug, edition_date, status, updated_at)
                VALUES (:id, :doc, 'the-hindu', '2026-07-31', 'indexing', :old)
                """
            ),
            {"id": str(edition_id), "doc": str(doc_id), "old": old},
        )
        db.commit()
    finally:
        db.close()
    return str(doc_id), str(edition_id)


def _cleanup(doc_id: str, edition_id: str) -> None:
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM qb.jobs WHERE payload->>'document_id' = :d"), {"d": doc_id})
        db.execute(text("DELETE FROM qb.newspaper_edition WHERE id = :id"), {"id": edition_id})
        doc = db.get(Document, uuid.UUID(doc_id))
        if doc:
            db.delete(doc)
        db.commit()
    finally:
        db.close()


def test_recovery_enqueues_cook_for_doc_ready_edition_stuck() -> None:
    """The recovery finds a doc-ready/edition-indexing orphan and re-enqueues a cook."""
    doc_id, edition_id = _seed_stuck_edition()
    try:
        from app.eta.schedules.newspaper import _recover_editions_doc_ready_uncooked

        n = _recover_editions_doc_ready_uncooked()
        assert n >= 1
        # A generate.questions job was enqueued for this document.
        db = SessionLocal()
        try:
            job = db.execute(
                text(
                    """
                    SELECT name FROM qb.jobs
                    WHERE payload->>'document_id' = :d AND name = 'generate.questions'
                    """
                ),
                {"d": doc_id},
            ).first()
        finally:
            db.close()
        assert job is not None
        assert job[0] == "generate.questions"
    finally:
        _cleanup(doc_id, edition_id)


def test_recovery_skips_edition_with_active_cook() -> None:
    """An edition that already has an active cook job must NOT be re-enqueued."""
    doc_id, edition_id = _seed_stuck_edition()
    db = SessionLocal()
    try:
        # Seed an active (queued) cook job so the liveness guard matches.
        db.execute(
            text(
                """
                INSERT INTO qb.jobs (name, status, workload, payload)
                VALUES ('generate.questions', 'queued', 'cpu',
                        CAST(:p AS jsonb))
                """
            ),
            {"p": f'{{"document_id": "{doc_id}"}}'},
        )
        db.commit()
    finally:
        db.close()
    try:
        from app.eta.schedules.newspaper import _recover_editions_doc_ready_uncooked

        _recover_editions_doc_ready_uncooked()
        # The active-cook edition is filtered out — no new enqueue for it.
        db = SessionLocal()
        try:
            dupes = db.execute(
                text(
                    """
                    SELECT count(*) FROM qb.jobs
                    WHERE payload->>'document_id' = :d AND name = 'generate.questions'
                    """
                ),
                {"d": doc_id},
            ).scalar()
        finally:
            db.close()
        assert dupes == 1  # only the seeded job, no duplicate from recovery
    finally:
        _cleanup(doc_id, edition_id)
