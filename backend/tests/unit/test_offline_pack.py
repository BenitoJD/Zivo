"""Offline pack pure logic: assembly, signing, tamper rejection (ADR 0006).

DB-free unit tests for ``offline_pack`` — the envelope shape, the HMAC
sign/verify round-trip, signature tamper rejection, and internal-key stripping.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.services.offline_pack import (
    PACK_SCHEMA_VERSION,
    _strip_internal_keys,
    assemble_pack,
    sign_pack,
    verify_pack,
)


def _raw_assertions() -> list[dict]:
    return [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "title": "Capitals",
            "summary": None,
            "payload": {
                "question": "Capital of France?",
                "options": ["Paris", "Lyon", "Nice"],
                "correct_index": 0,
                "explanation": "Paris is the capital.",
                "option_feedback": {"0": "Yes", "1": "No"},
                "primary_concept": "geography",
                "quality": {"should_be_stripped": True},
            },
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "title": None,
            "summary": "A multi-select item.",
            "payload": {
                "question": "Select primes.",
                "options": ["2", "3", "4"],
                "correct_index": 0,
                "correct_indices": [0, 1],
                "explanation": "2 and 3 are prime.",
                "quality_codes": ["x"],
            },
        },
    ]


def test_assemble_pack_shape() -> None:
    doc_id = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    pack = assemble_pack(
        document_id=doc_id,
        artifact={"id": str(doc_id), "filename": "x.pdf"},
        assertions=_raw_assertions(),
        mastery={"answered_ids": [], "current_page": 1, "budget_serve_mode": "learn"},
        serve_mode="learn",
        expires_at=expires,
    )
    assert pack["schema_version"] == PACK_SCHEMA_VERSION
    assert pack["question_count"] == 2
    assert len(pack["deck"]) == 2
    # Deck preserves input order and assigns a monotonic sequence.
    assert [c["sequence"] for c in pack["deck"]] == [0, 1]
    # Answer keys are present (the whole point of the pack).
    assert pack["deck"][0]["payload"]["correct_index"] == 0
    assert pack["deck"][1]["payload"]["correct_indices"] == [0, 1]
    assert pack["deck"][0]["payload"]["option_feedback"] == {"0": "Yes", "1": "No"}


def test_assemble_pack_strips_internal_keys_only() -> None:
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    pack = assemble_pack(
        document_id=uuid.uuid4(),
        artifact={},
        assertions=_raw_assertions(),
        mastery={},
        expires_at=expires,
    )
    p0 = pack["deck"][0]["payload"]
    # Internal QA bookkeeping is removed...
    assert "quality" not in p0
    p1 = pack["deck"][1]["payload"]
    assert "quality_codes" not in p1
    # ...but answer key + feedback survive (offline grading needs them).
    assert "correct_index" in p0
    assert "option_feedback" in p0


def test_sign_verify_round_trip() -> None:
    payload = {"a": 1, "b": [2, 3]}
    sig = sign_pack(payload, secret="test-secret")
    assert verify_pack(payload, sig, secret="test-secret") is True


def test_sign_is_deterministic() -> None:
    payload = {"b": 2, "a": 1, "c": [3, 2, 1]}
    # Canonical (sorted-key) serialization → same signature regardless of insertion order.
    assert sign_pack(payload, secret="s") == sign_pack(
        {"a": 1, "b": 2, "c": [3, 2, 1]}, secret="s"
    )


def test_verify_rejects_tampered_payload() -> None:
    sig = sign_pack({"a": 1}, secret="s")
    assert verify_pack({"a": 2}, sig, secret="s") is False


def test_verify_rejects_wrong_secret() -> None:
    sig = sign_pack({"a": 1}, secret="real")
    assert verify_pack({"a": 1}, sig, secret="forged") is False


def test_strip_internal_keys_empty_and_non_dict() -> None:
    assert _strip_internal_keys({}) == {}
    assert _strip_internal_keys(None) == {}  # type: ignore[arg-type]
    kept = _strip_internal_keys({"quality": 1, "question": "q?"})
    assert kept == {"question": "q?"}
