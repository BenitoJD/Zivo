"""Guest document upload without sign-in."""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Document, User
from app.services.auth import hash_password
from app.services.guest_session import GUEST_ID_HEADER


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
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("app.services.document_create.save_upload", lambda *_a, **_k: "demo/test/upload.txt")
    monkeypatch.setattr("app.services.document_create.enqueue_ingest", lambda _db, _doc_id: None)
    return TestClient(app)


def test_guest_upload_then_fetch_with_header_only(client: TestClient) -> None:
    created_ids: list = []

    try:
        upload = client.post(
            "/api/documents",
            files={"file": ("sample.txt", io.BytesIO(b"hello guest"), "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        body = upload.json()
        created_ids.append(body["id"])
        guest_id = upload.headers[GUEST_ID_HEADER]
        assert guest_id
        assert body["meta"]["guest_id"] == guest_id

        # No cookies — header alone must authorize reads.
        header_client = TestClient(app)
        got = header_client.get(
            f"/api/documents/{body['id']}",
            headers={GUEST_ID_HEADER: guest_id},
        )
        assert got.status_code == 200, got.text

        listed = header_client.get("/api/documents", headers={GUEST_ID_HEADER: guest_id})
        assert listed.status_code == 200
        assert len(listed.json()) == 1
    finally:
        db = SessionLocal()
        for doc_id in created_ids:
            doc = db.get(Document, doc_id)
            if doc:
                db.delete(doc)
        db.commit()
        db.close()


def test_guest_can_upload_image_after_non_image_cap(client: TestClient) -> None:
    created_ids: list = []

    try:
        first = client.post(
            "/api/documents",
            files={"file": ("notes.txt", io.BytesIO(b"hello guest"), "text/plain")},
        )
        assert first.status_code == 200, first.text
        created_ids.append(first.json()["id"])
        guest_id = first.headers[GUEST_ID_HEADER]

        second = client.post(
            "/api/documents",
            files={"file": ("photo.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
            headers={GUEST_ID_HEADER: guest_id},
        )
        assert second.status_code == 422, second.text

        third = client.post(
            "/api/documents",
            files={"file": ("more.txt", io.BytesIO(b"blocked"), "text/plain")},
            headers={GUEST_ID_HEADER: guest_id},
        )
        assert third.status_code == 409, third.text
    finally:
        db = SessionLocal()
        for doc_id in created_ids:
            doc = db.get(Document, doc_id)
            if doc:
                db.delete(doc)
        db.commit()
        db.close()


def test_guest_upload_claimed_on_login(client: TestClient) -> None:
    created_ids: list = []
    user_id: uuid.UUID | None = None
    username = f"guestclaim_{uuid.uuid4().hex[:8]}"

    try:
        upload = client.post(
            "/api/documents",
            files={"file": ("claim-me.txt", io.BytesIO(b"keep after login"), "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        doc_id = upload.json()["id"]
        created_ids.append(doc_id)
        guest_id = upload.headers[GUEST_ID_HEADER]

        db = SessionLocal()
        user = User(username=username, password_hash=hash_password("testpass123"))
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
        db.close()

        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": "testpass123", "remember_me": False},
            headers={GUEST_ID_HEADER: guest_id},
        )
        assert login.status_code == 200, login.text

        listed = client.get("/api/documents")
        assert listed.status_code == 200
        ids = [d["id"] for d in listed.json()]
        assert doc_id in ids

        db = SessionLocal()
        doc = db.get(Document, uuid.UUID(doc_id))
        assert doc is not None
        assert doc.account_id == user_id
        assert "guest_id" not in (doc.meta or {})
        db.close()
    finally:
        db = SessionLocal()
        for doc_id in created_ids:
            doc = db.get(Document, doc_id)
            if doc:
                db.delete(doc)
        if user_id:
            user = db.get(User, user_id)
            if user:
                db.delete(user)
        db.commit()
        db.close()
