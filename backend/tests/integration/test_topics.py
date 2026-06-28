"""Explain feature — topic outline storage round-trip (DB-gated, self-cleaning)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import Document
from app.services.topics import ensure_topics, load_outline, save_outline


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def _make_doc(db) -> uuid.UUID:
    doc_id = uuid.uuid4()
    db.add(
        Document(
            id=doc_id,
            slug=f"topics-{doc_id}",
            filename="t.pdf",
            content_type="application/pdf",
            size_bytes=10,
            storage_key=f"test/{doc_id}.pdf",
            status="ready",
            meta={"guest_id": "t" * 32, "page_count": 1},
        )
    )
    db.flush()
    return doc_id


def test_outline_save_load_round_trip() -> None:
    db = SessionLocal()
    try:
        did = _make_doc(db)
        assert load_outline(db, did)["status"] == "missing"
        topics = [
            {"key": "intro", "title": "Introduction", "summary": "the basics"},
            {"key": "deep", "title": "Going Deeper", "summary": "more"},
        ]
        save_outline(db, did, topics)
        state = load_outline(db, did)
        assert state["status"] == "ready"
        assert [t["title"] for t in state["topics"]] == ["Introduction", "Going Deeper"]
    finally:
        db.rollback()
        db.close()


def test_ensure_topics_returns_ready_without_re_enqueue() -> None:
    db = SessionLocal()
    try:
        did = _make_doc(db)
        save_outline(db, did, [{"key": "a", "title": "A", "summary": ""}])
        before = db.execute(
            text("SELECT count(*) FROM qb.jobs WHERE name='topics.generate' AND payload->>'document_id'=:i"),
            {"i": str(did)},
        ).scalar()
        state = ensure_topics(db, did)
        after = db.execute(
            text("SELECT count(*) FROM qb.jobs WHERE name='topics.generate' AND payload->>'document_id'=:i"),
            {"i": str(did)},
        ).scalar()
        assert state["status"] == "ready"
        assert after == before  # ready outline is served, not regenerated
    finally:
        db.rollback()
        db.close()
