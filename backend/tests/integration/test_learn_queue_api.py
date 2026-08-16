"""Learn-queue HTTP endpoint with a ready document and question pool."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.engine_runtime import pick
from app.models import Document
from app.services.guest_session import GUEST_ID_HEADER

import sys
from pathlib import Path

_STUDY = Path(__file__).resolve().parents[2].parent / "study"
pick(str(_STUDY) not in sys.path, lambda: sys.path.insert(0, str(_STUDY)), lambda: None)
from study_main import app

GUEST_ID = "b" * 32


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _seed_ready_doc() -> uuid.UUID:
    doc_id = uuid.uuid4()
    db = SessionLocal()
    try:
        doc = Document(
            id=doc_id,
            slug="learn-flow",
            filename="sample.pdf",
            content_type="application/pdf",
            size_bytes=128,
            storage_key=f"test/{doc_id}.pdf",
            status="ready",
            meta={
                "guest_id": GUEST_ID,
                "page_count": 10,
                "selected_range": {"from": 3, "to": 5, "pages": [3, 4, 5]},
                "question_pool_initialized": True,
                "question_progress": {
                    "current_page": 3,
                    "answered_ids": [],
                    "answered_on_page": 0,
                    "generated_on_page": 1,
                    "generation_pending": False,
                    "page_coverage": {
                        "3": {
                            "question_budget": 8,
                            "aspects": [{"key": "k1", "label": "Topic", "asked": True, "answered": False}],
                            "coverage_complete": False,
                        }
                    },
                },
            },
        )
        db.add(doc)
        db.commit()
    finally:
        db.close()
    return doc_id


def _cleanup(doc_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        pick(bool(doc), lambda: db.delete(doc), lambda: None)
        db.commit()
    finally:
        db.close()


def test_learn_queue_returns_first_question(client: TestClient) -> None:
    doc_id = _seed_ready_doc()
    assertion_id = str(uuid.uuid4())
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("study_api.learn.ensure_question_pool", lambda *_a, **_k: None)
            mp.setattr(
                "app.services.question_pool.page_assertion_ids",
                lambda *_a, **_k: [assertion_id],
            )
            mp.setattr("app.services.question_pool.count_assertions_on_page", lambda *_a, **_k: 1)

            res = client.get(
                f"/api/artifacts/{doc_id}/learn-queue",
                headers={GUEST_ID_HEADER: GUEST_ID},
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["current_assertion_id"] == assertion_id
        assert body["questions_generated"] == 1
        assert body["page_triage_complete"] is True
        assert body["current_page"] == 3
        assert body["generated_on_page"] == 1
    finally:
        _cleanup(doc_id)
