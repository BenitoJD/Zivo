"""Integration tests for learn-queue generation flow and progress persistence."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Document
from app.services.question_pool import (
    ensure_question_pool,
    get_page_coverage,
    get_progress,
    release_stuck_generation,
    save_page_coverage,
    save_progress,
)

pytest.importorskip("app.config")


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def _ready_doc(*, page: int = 17) -> tuple[uuid.UUID, SessionLocal]:
    db = SessionLocal()
    doc_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        slug="test",
        filename="flow.pdf",
        content_type="application/pdf",
        size_bytes=100,
        storage_key=f"test/{doc_id}.pdf",
        status="ready",
        meta={
            "guest_id": "a" * 32,
            "page_count": 50,
            "selected_range": {"from": page, "to": page + 2, "pages": [page, page + 1, page + 2]},
            "question_pool_initialized": True,
            "question_progress": {
                "current_page": page,
                "answered_ids": [],
                "answered_on_page": 0,
                "generated_on_page": 0,
                "generation_pending": False,
                "page_coverage": {},
            },
        },
    )
    db.add(doc)
    db.commit()
    return doc_id, db


def _cleanup(doc_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc:
            db.delete(doc)
            db.commit()
    finally:
        db.close()


def test_save_page_coverage_persists_through_generation_pending_poll() -> None:
    doc_id, db = _ready_doc()
    try:
        aspects = [{"key": "main", "label": "Main idea", "asked": False, "answered": False}]
        save_page_coverage(
            db,
            doc_id,
            page=17,
            question_budget=11,
            aspects=aspects,
            rationale="test",
        )

        doc = db.get(Document, doc_id)
        assert get_page_coverage(doc, 17)["question_budget"] == 11

        # Simulate learn-queue poll setting generation_pending without wiping coverage.
        save_progress(db, doc, {"generation_pending": True})
        db.commit()

        db.refresh(doc)
        cov = get_page_coverage(doc, 17)
        assert cov.get("question_budget") == 11
        assert len(cov.get("aspects") or []) == 1
        assert get_progress(doc)["generation_pending"] is True
    finally:
        db.close()
        _cleanup(doc_id)


def test_release_stuck_generation_does_not_cancel_healthy_queued_work() -> None:
    doc_id, db = _ready_doc()
    try:
        aspects = [{"key": "a", "label": "A", "asked": False, "answered": False}]
        save_page_coverage(db, doc_id, page=17, question_budget=8, aspects=aspects)
        doc = db.get(Document, doc_id)
        save_progress(db, doc, {"generation_pending": False})
        db.commit()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "app.services.question_pool._cancel_queued_generate_jobs",
                lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not cancel")),
            )
            release_stuck_generation(db, doc_id)

        db.refresh(doc)
        assert get_page_coverage(doc, 17)["question_budget"] == 8
    finally:
        db.close()
        _cleanup(doc_id)


def test_ensure_question_pool_enqueues_triage_when_coverage_missing() -> None:
    doc_id, db = _ready_doc()
    try:
        with pytest.MonkeyPatch.context() as mp:
            enqueued: list[str] = []

            def _fake_triage(_db, doc, *, page: int):
                enqueued.append(str(page))
                return None

            mp.setattr("app.services.question_pool_jobs.enqueue_page_triage", _fake_triage)
            mp.setattr("app.services.question_pool_jobs._has_active_generate_job", lambda *_a, **_k: False)
            mp.setattr("app.services.question_pool_jobs.release_stuck_generation", lambda *_a, **_k: None)
            mp.setattr("app.services.question_pool_jobs.next_assertion_id", lambda *_a, **_k: None)

            ensure_question_pool(db, doc_id)

        assert enqueued == ["17"]
    finally:
        db.close()
        _cleanup(doc_id)
