"""Recovery for background-prep documents stranded in the cooking phase.

Regression for the orphan state where a background-prep document finished
indexing and transitioned to ``status='prepping'`` (the cook phase), but the
cook chain died (generate.questions exhausted retries / worker pod terminated),
freezing the doc at its last ``prep_progress`` — which the sources sidebar
renders as "Prepping X%" indefinitely. See ``_recover_stuck_prepping_documents``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import Document

GUEST_ID = "f" * 32


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def _seed_stuck_prepping_doc() -> str:
    """Seed a background-prep document frozen at status='prepping', 48% progress."""
    doc_id = uuid.uuid4()
    db = SessionLocal()
    try:
        old = datetime.now(timezone.utc) - timedelta(minutes=30)
        doc = Document(
            id=doc_id,
            slug="stuck-bg-prep",
            filename="the-art-of-seduction.pdf",
            content_type="application/pdf",
            size_bytes=128,
            storage_key=f"test/{doc_id}.pdf",
            status="prepping",
            index_progress=48,
            updated_at=old,
            meta={
                "guest_id": GUEST_ID,
                "page_count": 6,
                "prep_mode": "background",
                "prep_complete": False,
                "prep_phase": "cooking",
                "selected_range": {"from": 1, "to": 6, "pages": [1, 2, 3, 4, 5, 6]},
                "question_pool_initialized": True,
                "ingested_pages": [1, 2, 3, 4, 5, 6],
                "question_progress": {
                    "current_page": 1,
                    "answered_ids": [],
                    "answered_on_page": 0,
                    "generated_on_page": 2,
                    "generation_pending": False,
                },
            },
        )
        db.add(doc)
        db.commit()
    finally:
        db.close()
    return str(doc_id)


def _cleanup(doc_id: str) -> None:
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM qb.jobs WHERE payload->>'document_id' = :d"), {"d": doc_id})
        doc = db.get(Document, uuid.UUID(doc_id))
        if doc:
            db.delete(doc)
        db.commit()
    finally:
        db.close()


def test_recovery_picks_up_prepping_background_prep_doc() -> None:
    """A prepping background-prep doc with no active jobs is selected by recovery."""
    doc_id = _seed_stuck_prepping_doc()
    try:
        from app.eta.schedules.ingest_recovery import _recover_stuck_prepping_documents

        _recover_stuck_prepping_documents()
        # Recovery called tick_background_cook → a generate.questions job was
        # enqueued (for the next uncooked page). Assert one exists now.
        db = SessionLocal()
        try:
            job = db.execute(
                text(
                    """
                    SELECT name FROM qb.jobs
                    WHERE payload->>'document_id' = :d
                      AND name = 'generate.questions'
                    """
                ),
                {"d": doc_id},
            ).first()
        finally:
            db.close()
        assert job is not None
        assert job[0] == "generate.questions"
    finally:
        _cleanup(doc_id)


def test_recovery_skips_doc_with_active_cook_job() -> None:
    """A prepping doc that already has an active cook job must NOT be re-enqueued."""
    doc_id = _seed_stuck_prepping_doc()
    db = SessionLocal()
    try:
        db.execute(
            text(
                """
                INSERT INTO qb.jobs (name, status, workload, payload)
                VALUES ('generate.questions', 'queued', 'cpu', CAST(:p AS jsonb))
                """
            ),
            {"p": f'{{"document_id": "{doc_id}"}}'},
        )
        db.commit()
    finally:
        db.close()
    try:
        from app.eta.schedules.ingest_recovery import _recover_stuck_prepping_documents

        _recover_stuck_prepping_documents()
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
        _cleanup(doc_id)


def test_recovery_skips_non_background_prepping_doc() -> None:
    """A prepping doc that is NOT background-prep (foreground) is left alone."""
    doc_id = uuid.uuid4()
    db = SessionLocal()
    try:
        old = datetime.now(timezone.utc) - timedelta(minutes=30)
        doc = Document(
            id=doc_id,
            slug="foreground-prep",
            filename="fg.pdf",
            content_type="application/pdf",
            size_bytes=128,
            storage_key=f"test/{doc_id}.pdf",
            status="prepping",
            index_progress=48,
            updated_at=old,
            meta={"guest_id": GUEST_ID, "page_count": 6},  # no prep_mode=background
        )
        db.add(doc)
        db.commit()
    finally:
        db.close()
    try:
        from app.eta.schedules.ingest_recovery import _recover_stuck_prepping_documents

        _recover_stuck_prepping_documents()
        # The foreground doc is filtered out by the prep_mode='background' guard.
        db = SessionLocal()
        try:
            jobs = db.execute(
                text(
                    """
                    SELECT count(*) FROM qb.jobs
                    WHERE payload->>'document_id' = :d AND name = 'generate.questions'
                    """
                ),
                {"d": str(doc_id)},
            ).scalar()
        finally:
            db.close()
        assert jobs == 0  # recovery did not enqueue anything for the foreground doc
    finally:
        _cleanup(str(doc_id))
