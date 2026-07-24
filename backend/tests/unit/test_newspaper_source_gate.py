"""Newspaper docs are is_public for MCQ practice, but source dumps stay admin-only."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.access import forbid_newspaper_source, require_document_source
from app.models import Document


def _doc(**meta: object) -> Document:
    d = Document(
        slug="news-test",
        filename="paper.pdf",
        content_type="application/pdf",
        size_bytes=10,
        storage_key="newspaper/x/paper.pdf",
        status="ready",
        meta=dict(meta),
    )
    d.id = uuid.uuid4()
    d.account_id = None
    return d


def test_forbid_newspaper_source_blocks_learners() -> None:
    doc = _doc(is_public=True, newspaper=True, hide_source=True, ingest_kind="newspaper")
    with pytest.raises(HTTPException) as exc:
        forbid_newspaper_source(doc, user=None)
    assert exc.value.status_code == 404

    learner = SimpleNamespace(is_admin=False)
    with pytest.raises(HTTPException) as exc:
        forbid_newspaper_source(doc, user=learner)  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_forbid_newspaper_source_allows_admin() -> None:
    doc = _doc(is_public=True, newspaper=True, hide_source=True)
    admin = SimpleNamespace(is_admin=True)
    forbid_newspaper_source(doc, user=admin)  # type: ignore[arg-type]


def test_forbid_newspaper_source_allows_normal_docs() -> None:
    doc = _doc(guest_id="abc")
    forbid_newspaper_source(doc, user=None)


def test_require_document_source_404_for_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDB:
        def get(self, _model, _id):  # noqa: ANN001
            return None

    with pytest.raises(HTTPException) as exc:
        require_document_source(FakeDB(), uuid.uuid4(), None, None)  # type: ignore[arg-type]
    assert exc.value.status_code == 404
