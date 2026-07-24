"""Practice newspaper window: learners only see / open last RETENTION_DAYS."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.access import forbid_stale_newspaper_practice
from app.models import Document
from app.repositories import newspaper as newspaper_repo
from app.services.newspaper import edition_in_practice_window, window_start


def test_window_start_is_retention_days_back() -> None:
    today = date(2026, 7, 24)
    assert window_start(today) == today - timedelta(days=newspaper_repo.RETENTION_DAYS)
    assert newspaper_repo.RETENTION_DAYS == 30


def test_edition_in_practice_window_boundary() -> None:
    today = date(2026, 7, 24)
    since = window_start(today)
    assert edition_in_practice_window(since, today=today)
    assert edition_in_practice_window(today, today=today)
    assert not edition_in_practice_window(since - timedelta(days=1), today=today)
    assert not edition_in_practice_window(today - timedelta(days=31), today=today)


def _news_doc(edition_date: str | None) -> Document:
    meta: dict = {
        "is_public": True,
        "newspaper": True,
        "hide_source": True,
        "ingest_kind": "newspaper",
        "paper_slug": "the-hindu",
        "paper_title": "The Hindu",
    }
    if edition_date is not None:
        meta["edition_date"] = edition_date
    d = Document(
        slug="news-win",
        filename="paper.pdf",
        content_type="application/pdf",
        size_bytes=10,
        storage_key="newspaper/x/paper.pdf",
        status="ready",
        meta=meta,
    )
    d.id = uuid4()
    d.account_id = None
    return d


def test_forbid_stale_newspaper_blocks_learners() -> None:
    # Far enough back that wall-clock CI cannot land inside the 30-day window.
    doc = _news_doc((date.today() - timedelta(days=400)).isoformat())
    with pytest.raises(HTTPException) as exc:
        forbid_stale_newspaper_practice(doc, user=None)
    assert exc.value.status_code == 404

    learner = SimpleNamespace(is_admin=False)
    with pytest.raises(HTTPException) as exc:
        forbid_stale_newspaper_practice(doc, user=learner)  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_forbid_stale_newspaper_allows_admin() -> None:
    doc = _news_doc((date.today() - timedelta(days=400)).isoformat())
    admin = SimpleNamespace(is_admin=True)
    forbid_stale_newspaper_practice(doc, user=admin)  # type: ignore[arg-type]


def test_forbid_stale_newspaper_allows_fresh() -> None:
    doc = _news_doc(date.today().isoformat())
    forbid_stale_newspaper_practice(doc, user=None)


def test_forbid_stale_newspaper_skips_normal_docs() -> None:
    d = Document(
        slug="upload",
        filename="notes.pdf",
        content_type="application/pdf",
        size_bytes=10,
        storage_key="uploads/x.pdf",
        status="ready",
        meta={"edition_date": "2000-01-01"},
    )
    d.id = uuid4()
    forbid_stale_newspaper_practice(d, user=None)
