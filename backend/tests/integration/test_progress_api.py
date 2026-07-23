"""Learner progress API — empty journal + auth scoping."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.services.guest_session import GUEST_ID_HEADER

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
