"""Enqueue dedup: a backed-up queue must not breed duplicate jobs.

Regression guard for the 2026-08-09 incident — recovery schedulers re-enqueue
whenever no *live* job exists, and a queued job goes "stale" after 30 min, so a
slow queue bred a fresh duplicate set every tick (1,709 copies across 8 docs).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.jobs import _dedupable, _payload_key, batch_enqueue_jobs


def _spec(page: int, doc: str = "doc-1", name: str = "ingest.page") -> dict:
    return {"name": name, "payload": {"document_id": doc, "page_number": page}}


def _db_with_queued(rows: list[tuple[str, dict]]) -> MagicMock:
    db = MagicMock()
    db.execute.return_value.all.return_value = rows
    return db


def test_payload_key_is_order_independent() -> None:
    a = _payload_key("ingest.page", {"document_id": "d", "page_number": 2})
    b = _payload_key("ingest.page", {"page_number": 2, "document_id": "d"})
    assert a == b
    assert a != _payload_key("ingest.page", {"document_id": "d", "page_number": 3})
    assert a != _payload_key("generate.questions", {"document_id": "d", "page_number": 2})


def test_dag_jobs_are_never_deduped() -> None:
    assert _dedupable(_spec(1))
    assert not _dedupable({**_spec(1), "execution_id": "e1"})
    assert not _dedupable({**_spec(1), "parent_job_ids": ["j1"]})


def test_batch_enqueue_skips_specs_already_queued() -> None:
    db = _db_with_queued([("ingest.page", {"document_id": "doc-1", "page_number": 1})])
    with patch("app.eta.submit.build_job", side_effect=lambda **kw: MagicMock(**kw)):
        jobs = batch_enqueue_jobs(db, [_spec(1), _spec(2)])
    # Page 1 was already waiting; only page 2 is new.
    assert len(jobs) == 1
    assert db.add.call_count == 1


def test_batch_enqueue_dedups_repeats_within_one_call() -> None:
    db = _db_with_queued([])
    with patch("app.eta.submit.build_job", side_effect=lambda **kw: MagicMock(**kw)):
        jobs = batch_enqueue_jobs(db, [_spec(3), _spec(3), _spec(3)])
    assert len(jobs) == 1


def test_batch_enqueue_keeps_distinct_work() -> None:
    db = _db_with_queued([])
    specs = [_spec(1), _spec(2), _spec(1, doc="doc-2")]
    with patch("app.eta.submit.build_job", side_effect=lambda **kw: MagicMock(**kw)):
        jobs = batch_enqueue_jobs(db, specs)
    assert len(jobs) == 3
