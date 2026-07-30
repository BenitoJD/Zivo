"""Registry default-mutation is serialized across concurrent boots (pods).

The partial-unique index ``uq_llm_models_default_per_kind`` allows exactly one
``is_default = true`` chat row. During a rolling update two API pods can boot at
once and both run ``ensure_registry_providers`` + ``sync_default_chat_model_from_env``:
each clears the existing default and sets a new one within one transaction.
Without serialization, both flushes together emit two ``is_default = true`` rows
and trip the unique constraint, aborting the whole registry bootstrap.

This proves the transaction-scoped advisory lock serializes those mutations: two
independent sessions (two pods) flipping the default to two different models
concurrently leaves exactly one default and raises no IntegrityError.

Auto-skips when the dev DB is not reachable (mirrors test_rate_limit_db.py).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.llm import LlmModel, LlmModelKind
from app.services.llm_registry import (
    _REGISTRY_LOCK_KEY,
    _acquire_registry_lock,
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


def _enabled_chat_models(db) -> list[LlmModel]:
    return (
        db.query(LlmModel)
        .filter(LlmModel.kind == LlmModelKind.chat)
        .order_by(LlmModel.sort_order)
        .all()
    )


@pytest.fixture(autouse=True)
def _restore_default():
    """Snapshot the default before each test and restore it after, so the test's
    own default flips never leak into the live dev registry."""
    db = SessionLocal()
    original = (
        db.query(LlmModel)
        .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.is_default.is_(True))
        .first()
    )
    db.close()
    yield
    if original is not None:
        db = SessionLocal()
        target = db.get(LlmModel, original.id)
        if target is not None:
            _acquire_registry_lock(db)
            db.query(LlmModel).filter(
                LlmModel.kind == LlmModelKind.chat, LlmModel.is_default.is_(True)
            ).update({"is_default": False}, synchronize_session="fetch")
            target.is_default = True
            db.commit()
        db.close()


def test_concurrent_default_flips_leave_exactly_one() -> None:
    """Two pods flipping the default to two different models concurrently.

    Without the advisory lock, both sessions commit a distinct is_default=True
    row and the partial-unique index aborts one transaction. With the lock, the
    two flips serialize: exactly one default survives, no IntegrityError raised.
    """
    db = SessionLocal()
    models = _enabled_chat_models(db)
    db.close()
    # Need at least two non-vision-only chat models to flip between.
    candidates = [m for m in models if not m.vision_only]
    assert len(candidates) >= 2, "test needs >=2 chat models in the registry"

    a_id, b_id = candidates[0].id, candidates[1].id
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def flip(target_id):
        try:
            db = SessionLocal()
            barrier.wait()  # release both threads together -> true concurrency
            _acquire_registry_lock(db)
            db.query(LlmModel).filter(
                LlmModel.kind == LlmModelKind.chat, LlmModel.is_default.is_(True)
            ).update({"is_default": False}, synchronize_session="fetch")
            db.get(LlmModel, target_id).is_default = True
            db.commit()
            db.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(flip, a_id)
        pool.submit(flip, b_id)

    assert not any(
        "uq_llm_models_default_per_kind" in str(e) for e in errors
    ), f"advisory lock did not serialize — IntegrityError leaked: {errors}"

    db = SessionLocal()
    defaults = (
        db.query(LlmModel)
        .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.is_default.is_(True))
        .all()
    )
    db.close()
    assert len(defaults) == 1, f"expected exactly one default, got {len(defaults)}"


def test_advisory_lock_key_is_stable_int() -> None:
    """The lock key is a fixed integer so every pod contends on the same lock."""
    assert isinstance(_REGISTRY_LOCK_KEY, int)
    # pg_advisory_xact_lock accepts a bigint; sanity-check the key is in range.
    assert -(2**63) <= _REGISTRY_LOCK_KEY <= 2**63 - 1
