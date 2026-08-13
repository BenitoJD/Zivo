"""Learner progress API — empty journal + auth scoping."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import Account
from app.services.answer_signal import record_answer_signal, resolve_subject_entity
from app.services.guest_session import GUEST_ID_HEADER
from app.services.progress import build_learner_progress
from app.repositories.intel import concept_id

import sys
from pathlib import Path

_STUDY = Path(__file__).resolve().parents[2].parent / "study"
if str(_STUDY) not in sys.path:
    sys.path.insert(0, str(_STUDY))
from study_main import app

GUEST_ID = "c" * 32


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


def test_progress_empty_for_guest(client: TestClient) -> None:
    res = client.get("/api/progress", headers={GUEST_ID_HEADER: GUEST_ID})
    assert res.status_code == 200
    body = res.json()
    assert body["first_attempt_only"] is True
    assert body["answers"]["total"] == 0
    assert body["questions_asked"] == 0
    assert body["recent"] == []
    assert body["sources"] == []
    assert body["topics"] == []


def _make_assertion(db, *, artifact_id: uuid.UUID) -> uuid.UUID:
    assertion_id = uuid.uuid4()
    source_id = db.execute(
        text("SELECT id FROM intel.source WHERE slug = 'user-upload'")
    ).scalar()
    db.execute(
        text(
            """
            INSERT INTO intel.assertion (
              id, type_concept_id, source_id, canonical_uri, fingerprint,
              title, summary, payload, status
            )
            VALUES (
              :id, :type_id, :source_id, :uri, :fp,
              :title, :summary, CAST(:payload AS jsonb), 'active'
            )
            """
        ),
        {
            "id": assertion_id,
            "type_id": concept_id(db, "/vocab/assertion/question.mcq"),
            "source_id": source_id,
            "uri": f"qb://assertion/progress-test/{assertion_id}",
            "fp": f"progress-test:{assertion_id}",
            "title": "Progress test?",
            "summary": "Test.",
            "payload": json.dumps(
                {
                    "artifact_id": str(artifact_id),
                    "page_number": 1,
                    "sequence": 0,
                    "primary_concept_key": "k",
                }
            ),
        },
    )
    return assertion_id


def test_progress_includes_graded_answer_for_account_without_prior_entity() -> None:
    db = SessionLocal()
    try:
        account_id = uuid.uuid4()
        username = f"prog_{account_id.hex[:10]}"
        db.execute(
            text(
                """
                INSERT INTO qb.account (id, username)
                VALUES (:id, :username)
                """
            ),
            {"id": account_id, "username": username},
        )
        user = Account(id=account_id, username=username)
        subject = resolve_subject_entity(db, user, None)
        assert subject is not None

        artifact_id = uuid.uuid4()
        item = _make_assertion(db, artifact_id=artifact_id)
        record_answer_signal(
            db,
            subject_entity_id=subject,
            assertion_id=item,
            correct=True,
            choice_index=0,
        )
        db.commit()

        body = build_learner_progress(
            db,
            subject_entity_id=subject,
            account_id=account_id,
            guest_id=None,
            artifact_id=artifact_id,
        )
        assert body["answers"]["total"] == 1
        assert body["answers"]["correct"] == 1
    finally:
        db.rollback()
        db.close()
