"""Document file download redirects to a browser-reachable presigned URL."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Document
from app.services.guest_session import GUEST_ID_HEADER

PUBLIC_URL = (
    "https://s3.citepage.example/citepage/demo/test/file.pdf"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
)


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
    monkeypatch.setattr("app.services.document_create.save_upload", lambda *_a, **_k: "demo/test/file.pdf")
    monkeypatch.setattr("app.services.document_create.enqueue_ingest", lambda _db, _doc_id: None)
    monkeypatch.setattr("app.api.documents.presigned_get_url", lambda _key: PUBLIC_URL)
    return TestClient(app)


def test_document_file_redirects_to_public_https(client: TestClient) -> None:
    doc_id: uuid.UUID | None = None
    guest_id = "0123456789abcdef0123456789abcdef"

    try:
        db = SessionLocal()
        doc = Document(
            slug="test-pdf",
            filename="test.pdf",
            content_type="application/pdf",
            size_bytes=16,
            storage_key="demo/test/file.pdf",
            status="ready",
            meta={"guest_id": guest_id},
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
        db.close()

        res = client.get(
            f"/api/documents/{doc_id}/file",
            headers={GUEST_ID_HEADER: guest_id},
            follow_redirects=False,
        )
        assert res.status_code in (307, 302), res.text
        assert res.headers["location"] == PUBLIC_URL
        assert res.headers["location"].startswith("https://s3.citepage.example/")
    finally:
        if doc_id:
            db = SessionLocal()
            row = db.get(Document, doc_id)
            if row:
                db.delete(row)
                db.commit()
            db.close()
