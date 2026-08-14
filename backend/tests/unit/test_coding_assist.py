"""Coding assist endpoints + invalid language on run."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from practice_api import coding as coding_api
from app.db import get_db
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.rate_limit import rate_limit_dependency


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(coding_api.router, prefix="/api/coding")

    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_optional_user] = lambda: None
    app.dependency_overrides[guest_session_for_read] = lambda: "g" * 32
    app.dependency_overrides[optional_guest_session] = lambda: "g" * 32
    app.dependency_overrides[require_csrf_or_guest] = lambda: None
    app.dependency_overrides[rate_limit_dependency] = lambda: None
    return TestClient(app)


def _problem() -> dict:
    return {
        "id": uuid.uuid4(),
        "payload": {
            "format": "qb.coding.v1",
            "title": "Fizz",
            "statement": "Print fizz",
            "sample_tests": [{"stdin": "1", "expected_output": "1"}],
            "hidden_tests": [{"stdin": "1", "expected_output": "1"}],
            "difficulty": "easy",
            "tags": ["loops"],
        },
        "title": "Fizz",
        "recorded_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "artifact_id": None,
    }


def test_run_rejects_unknown_language(client: TestClient) -> None:
    assertion_id = uuid.uuid4()
    with patch.object(coding_api, "_load_coding_assertion", return_value=_problem()):
        resp = client.post(
            f"/api/coding/{assertion_id}/run",
            json={"source": "print(1)", "language_id": 9999, "stdin": ""},
        )
    assert resp.status_code == 400
    assert "Unsupported language_id" in resp.json()["detail"]


def test_meta_languages_returns_top10(client: TestClient) -> None:
    resp = client.get("/api/coding/meta/languages")
    assert resp.status_code == 200
    langs = resp.json()["languages"]
    assert len(langs) == 10
    ids = {row["id"] for row in langs}
    assert 71 in ids and 73 in ids and 78 in ids


def test_assist_messages_lists_empty_thread(client: TestClient) -> None:
    assertion_id = uuid.uuid4()
    with (
        patch.object(coding_api, "_require_solvable_problem", return_value=_problem()),
        patch.object(coding_api, "list_assist_messages", return_value=[]),
    ):
        resp = client.get(f"/api/coding/{assertion_id}/assist/messages")
    assert resp.status_code == 200
    assert resp.json() == []


def test_assist_clear_bumps_version(client: TestClient) -> None:
    assertion_id = uuid.uuid4()
    with (
        patch.object(coding_api, "_require_solvable_problem", return_value=_problem()),
        patch.object(coding_api, "clear_assist_thread", return_value=2),
    ):
        resp = client.post(f"/api/coding/{assertion_id}/assist/clear", json={})
    assert resp.status_code == 200
    assert resp.json()["version"] == 2


def test_assist_stream_setup(client: TestClient) -> None:
    assertion_id = uuid.uuid4()
    fake_setup = {
        "thread_id": uuid.uuid4(),
        "prior_messages": [],
        "request_message": "hint?",
        "problem_block": "Title: Fizz",
        "editor_block": "",
    }

    async def fake_stream(_setup: dict):
        from sse_starlette.sse import EventSourceResponse

        async def gen():
            yield {"event": "done", "data": "{}"}

        return EventSourceResponse(gen())

    with (
        patch.object(coding_api, "_require_solvable_problem", return_value=_problem()),
        patch.object(coding_api, "public_payload", return_value={"title": "Fizz", "statement": "x", "sample_tests": [], "tags": [], "difficulty": "easy"}),
        patch.object(coding_api, "prepare_assist_stream", return_value=fake_setup),
        patch.object(coding_api, "assist_event_stream", new=AsyncMock(side_effect=fake_stream)),
    ):
        resp = client.post(
            f"/api/coding/{assertion_id}/assist",
            json={"message": "hint?", "code": "print(1)", "language_id": 71},
        )
    assert resp.status_code == 200
