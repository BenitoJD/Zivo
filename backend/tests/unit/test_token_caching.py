"""Unit tests for token/cost caching helpers."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.services.chunk_map_cache import chunk_map_key, content_hash_key, triage_cache_key, verify_verdict_key
from app.services.generation_cache import batch_drafts_key, page_context_key, purge_for_document
from app.services.source_fingerprint import chunks_fingerprint


def test_chunk_map_key_stable_for_same_inputs() -> None:
    mid = uuid.uuid4()
    a = chunk_map_key("hello chunk", prompt_key="notes_map:v1", model_id=mid)
    b = chunk_map_key("hello chunk", prompt_key="notes_map:v1", model_id=mid)
    assert a == b
    assert a != chunk_map_key("hello chunk", prompt_key="topics_map:v1", model_id=mid)


def test_batch_drafts_key_includes_model_and_content_type() -> None:
    mid = uuid.uuid4()
    targets = [{"key": "a"}, {"key": "b"}]
    base = batch_drafts_key(1, targets, None, model_id=mid, content_type="prose")
    other_model = batch_drafts_key(1, targets, None, model_id=uuid.uuid4(), content_type="prose")
    other_type = batch_drafts_key(1, targets, None, model_id=mid, content_type="code")
    assert base != other_model
    assert base != other_type


def test_verify_and_triage_keys_change_with_content() -> None:
    mid = uuid.uuid4()
    a = verify_verdict_key(
        stem="Q?", options=["a", "b"], correct_index=0, page_text="page", model_id=mid
    )
    b = verify_verdict_key(
        stem="Q?", options=["a", "b"], correct_index=0, page_text="other", model_id=mid
    )
    assert a != b
    assert triage_cache_key("page", 1) != triage_cache_key("other", 1)


def test_content_hash_key_orders_parts() -> None:
    assert content_hash_key("k", "a", "b") == content_hash_key("k", "a", "b")
    assert content_hash_key("k", "a", "b") != content_hash_key("k", "b", "a")


def test_page_context_key_format() -> None:
    did = uuid.uuid4()
    assert page_context_key(did, 3) == f"ctx:{did}:3"


def test_purge_for_document_targets_page_context() -> None:
    did = uuid.uuid4()
    db = MagicMock()
    db.execute.return_value.rowcount = 2
    assert purge_for_document(db, did) == 2
    sql = str(db.execute.call_args[0][0])
    assert "page_context" in sql
    assert db.execute.call_args[0][1]["prefix"] == f"ctx:{did}:%"


def test_chunks_fingerprint_empty_document() -> None:
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    fp = chunks_fingerprint(db, uuid.uuid4())
    assert isinstance(fp, str) and len(fp) == 64


def test_chunks_fingerprint_changes_with_hashes() -> None:
    db = MagicMock()
    db.execute.return_value.all.return_value = [("aaa",), ("bbb",)]
    a = chunks_fingerprint(db, uuid.uuid4())
    db.execute.return_value.all.return_value = [("aaa",), ("ccc",)]
    b = chunks_fingerprint(db, uuid.uuid4())
    assert a != b


def test_is_artifact_stale_when_missing_fp() -> None:
    from app.services.source_fingerprint import is_artifact_stale

    doc = MagicMock()
    doc.meta = {}
    db = MagicMock()
    db.get.return_value = doc
    db.execute.return_value.all.return_value = [("hash1",)]
    assert is_artifact_stale(db, uuid.uuid4(), "notes:notes") is True


def test_critic_sample_rate_default_is_fractional() -> None:
    from app.services import mcq_quality

    assert 0.0 < mcq_quality.CRITIC_SAMPLE_RATE <= 1.0
    # Default env is 0.3; allow override via ZIVO_CRITIC_SAMPLE_RATE.
    assert mcq_quality.CRITIC_SAMPLE_RATE <= 1.0
