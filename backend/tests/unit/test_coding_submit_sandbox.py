"""Coding submit must not invent teach-gap when the sandbox is down."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from practice_api import coding as coding_api
from app.db import get_db
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest_session import guest_session_for_read
from app.services.rate_limit import rate_limit_dependency


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(coding_api.router, prefix="/api/coding")

    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_optional_user] = lambda: None
    app.dependency_overrides[guest_session_for_read] = lambda: "g" * 32
    app.dependency_overrides[require_csrf_or_guest] = lambda: None
    app.dependency_overrides[rate_limit_dependency] = lambda: None
    return TestClient(app)


def test_submit_skips_teach_gap_when_sandbox_unavailable(client: TestClient) -> None:
    assertion_id = uuid.uuid4()
    problem = {
        "payload": {
            "title": "Array Max",
            "hidden_tests": [{"stdin": "1\n1", "expected_output": "1"}],
        }
    }

    with (
        patch.object(coding_api, "_load_coding_assertion", return_value=problem),
        patch.object(
            coding_api,
            "run_tests",
            new=AsyncMock(
                return_value={
                    "passed": 0,
                    "total": 1,
                    "cases": [],
                    "error": "code sandbox unavailable: boom",
                }
            ),
        ),
        patch.object(coding_api, "resolve_subject_entity", return_value=None),
        patch.object(coding_api, "record_coding_submit") as record,
        patch.object(coding_api, "teach_after_submit", new=AsyncMock()) as teach,
    ):
        resp = client.post(
            f"/api/coding/{assertion_id}/submit",
            json={"source": "print(1)", "language_id": 71},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] == "code sandbox unavailable: boom"
    assert body["mentor_summary"] is None
    assert body["lesson"] is None
    assert body["weak_concepts"] == []
    record.assert_not_called()
    teach.assert_not_called()
