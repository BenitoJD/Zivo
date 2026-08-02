"""Tests for small services previously at 0% coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.services.chunking import chunk_pages
from app.services.demo_seed import DEMO_DOC_ID, DEMO_PAGES, embed_demo_chunks_if_needed, ensure_demo_document
from app.services.job_retention import cleanup_terminal_jobs


# --- chunk_pages ------------------------------------------------------------


def test_chunk_pages_short_page_kept_whole() -> None:
    out = chunk_pages([{"page": 1, "text": "hello world"}], max_chars=900)
    assert out == [{"page_start": 1, "page_end": 1, "text": "hello world"}]


def test_chunk_pages_long_page_split_with_overlap() -> None:
    text = "a" * 2000
    out = chunk_pages([{"page": 2, "text": text}], max_chars=900, overlap=120)
    assert len(out) > 1
    assert all(c["page_start"] == 2 and c["page_end"] == 2 for c in out)
    # Every char appears in at least one chunk (pieces cover the full text).
    assert len(out[0]["text"]) == 900
    assert sum(len(c["text"]) for c in out) > 2000
    # Overlap: consecutive pieces share the tail of the previous one.
    assert out[1]["text"].startswith(out[0]["text"][-120:])


def test_chunk_pages_empty_or_blank_text_skipped() -> None:
    assert chunk_pages([{"page": 1, "text": ""}]) == []
    assert chunk_pages([{"page": 1, "text": "   "}]) == []
    assert chunk_pages([{"page": 1, "text": None}]) == []


def test_chunk_pages_multiple_pages() -> None:
    out = chunk_pages([{"page": 1, "text": "one"}, {"page": 2, "text": "two"}])
    assert [c["page_start"] for c in out] == [1, 2]


# --- job_retention ----------------------------------------------------------


def test_cleanup_terminal_jobs_deletes_old_rows() -> None:
    db = MagicMock()
    db.execute.return_value.rowcount = 3
    n = cleanup_terminal_jobs(db, max_age_days=7)
    assert n == 3
    db.execute.assert_called_once()
    sql = str(db.execute.call_args[0][0])
    assert "DELETE FROM qb.jobs" in sql
    assert "finished_at < :cutoff" in sql
    cutoff = db.execute.call_args[0][1]["cutoff"]
    assert isinstance(cutoff, datetime)
    assert cutoff <= datetime.now(timezone.utc)
    assert cutoff >= datetime.now(timezone.utc) - timedelta(days=8)
    db.commit.assert_called_once()


def test_cleanup_terminal_jobs_zero_rows() -> None:
    db = MagicMock()
    db.execute.return_value.rowcount = 0
    assert cleanup_terminal_jobs(db) == 0
    db.commit.assert_called_once()


# --- demo_seed --------------------------------------------------------------


def test_ensure_demo_document_skips_when_exists() -> None:
    db = MagicMock()
    db.get.return_value = object()  # doc exists
    assert ensure_demo_document(db) is False
    db.add.assert_not_called()


def test_ensure_demo_document_creates_when_missing() -> None:
    db = MagicMock()
    db.get.return_value = None
    created = ensure_demo_document(db)
    assert created is True
    # Document + one chunk per demo page.
    assert db.add.call_count == 1 + len(DEMO_PAGES)
    doc = db.add.call_args_list[0][0][0]
    assert doc.id == DEMO_DOC_ID
    assert doc.slug == "sample-report"
    assert doc.meta == {"is_demo": True}
    db.commit.assert_called_once()


def test_embed_demo_chunks_if_needed_no_doc() -> None:
    db = MagicMock()
    db.__enter__.return_value = db
    db.get.return_value = None
    with patch("app.services.demo_seed.SessionLocal", return_value=db):
        embed_demo_chunks_if_needed()
    db.query.assert_not_called()


def test_embed_demo_chunks_if_needed_all_embedded() -> None:
    db = MagicMock()
    db.__enter__.return_value = db  # `with SessionLocal() as db:` yields the mock
    db.get.return_value = object()
    # .all() returns empty → "not missing" → return without embedding.
    db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []
    with patch("app.services.demo_seed.SessionLocal", return_value=db):
        embed_demo_chunks_if_needed()
    db.execute.assert_not_called()
    db.commit.assert_not_called()


def test_embed_demo_chunks_if_needed_updates_missing() -> None:
    from types import SimpleNamespace

    db = MagicMock()
    db.__enter__.return_value = db  # `with SessionLocal() as db:` yields the mock
    db.get.return_value = object()
    m1 = SimpleNamespace(id=1, text="one")
    m2 = SimpleNamespace(id=2, text="two")
    missing = [m1, m2]
    # The query chain returns `missing` — use a dedicated query mock whose
    # filter/order_by return itself so .all() resolves to our list.
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.all.return_value = missing
    db.query.return_value = q
    vectors = [[0.1, 0.2], [0.3, 0.4]]
    with (
        patch("app.services.demo_seed.SessionLocal", return_value=db),
        patch("app.services.demo_seed.embed_texts", return_value=vectors) as embed,
        patch("app.services.demo_seed.pgvector_literal", side_effect=lambda v: f"vec:{v}") as lit,
    ):
        embed_demo_chunks_if_needed()
    embed.assert_called_once_with(["one", "two"])
    assert lit.call_count == 2
    assert db.execute.call_count == 2
    db.commit.assert_called_once()
