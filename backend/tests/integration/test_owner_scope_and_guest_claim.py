"""Integration coverage for the two prod "Database unavailable" 503 fixes.

Both bugs were masked by the generic ``SQLAlchemyError`` handler in
``app.main`` and only surfaced as ``503 {"detail": "Database unavailable"}``.
They share no code path; they are covered together here because both require a
live Postgres to reproduce (the unit suite is DB-free).

1. ``owner_scope_sql()`` — every document open hit
   ``psycopg.errors.AmbiguousParameter: could not determine data type of
   parameter $3`` because ``:gid``/``:uid`` were bound as untyped NULLs.
   Exercised here through ``saved_notes.list_notes`` and ``brainstorm.list_ideas``
   for the logged-in, guest, and legacy (both-None) cases.

2. ``_claim_guest_measurements()`` — guest-who-practiced signup hit
   ``psycopg.errors.RaiseException: intel.measurement is immutable (INSERT only)``
   because it UPDATEd/DELETEd an INSERT-only table. Exercised here through
   ``claim_guest_progress`` with a real guest measurement row, and checked for
   idempotency on a second call.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal
from app.repositories.intel import concept_id
from app.services import brainstorm as brainstorm_service
from app.services import saved_notes as saved_notes_service
from app.services.answer_signal import record_answer_signal, resolve_subject_entity
from app.services.guest import _claim_guest_measurements, claim_guest_progress
from app.services.owner_scope import owner_scope_sql

GUEST_ID = "d" * 32


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def _make_assertion(db, *, artifact_id: uuid.UUID) -> uuid.UUID:
    assertion_id = uuid.uuid4()
    source_id = db.execute(
        text("SELECT id FROM intel.source WHERE slug = 'user-upload'")
    ).scalar()
    db.execute(
        text(
            """
            INSERT INTO intel.assertion (
              id, type_concept_id, source_id, canonical_uri, fingerprint,
              title, summary, payload, status
            )
            VALUES (
              :id, :type_id, :source_id, :uri, :fp,
              :title, :summary, CAST(:payload AS jsonb), 'active'
            )
            """
        ),
        {
            "id": assertion_id,
            "type_id": concept_id(db, "/vocab/assertion/question.mcq"),
            "source_id": source_id,
            "uri": f"qb://assertion/scope-test/{assertion_id}",
            "fp": f"scope-test:{assertion_id}",
            "title": "Scope test?",
            "summary": "Test.",
            "payload": json.dumps(
                {
                    "artifact_id": str(artifact_id),
                    "page_number": 1,
                    "sequence": 0,
                    "primary_concept_key": "k",
                }
            ),
        },
    )
    return assertion_id


def _make_document(db) -> uuid.UUID:
    """Insert a minimal qb.documents row so the document_saved_notes /
    document_brainstorm_ideas FKs are satisfied."""
    doc_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO qb.documents (id, slug, filename, content_type, size_bytes, storage_key)
            VALUES (:id, :slug, :filename, 'text/plain', 1, 'scope-test/blank')
            """
        ),
        {
            "id": doc_id,
            "slug": f"scope-test-{doc_id.hex[:10]}",
            "filename": "scope-test.txt",
        },
    )
    return doc_id


# ---------------------------------------------------------------------------
# Fix 1: owner_scope_sql() — typed NULLs
# ---------------------------------------------------------------------------


def test_owner_scope_sql_binds_untyped_nulls_without_ambiguity():
    """The exact regression: psycopg3 sends a NULL param as untyped, and the old
    ``(:uid IS NOT NULL AND ...)`` form gave Postgres no type to infer — raising
    ``could not determine data type of parameter $3``.

    We check param type resolution for all three cases (user set, guest set,
    both None) by wrapping the predicate in a CTE that supplies typed NULL
    columns, so Postgres must fully resolve every parameter's type. The real
    end-to-end query is exercised by test_list_notes_* / test_list_ideas_*
    below against the actual table."""
    predicate = owner_scope_sql()
    wrapped = (
        "WITH probe(document_id, account_id, guest_id) AS "
        "(VALUES (NULL::uuid, NULL::uuid, NULL::text)) "
        f"SELECT 1 FROM probe WHERE {predicate}"
    )
    db = SessionLocal()
    try:
        for params in (
            {"d": uuid.uuid4(), "uid": uuid.uuid4(), "gid": None},  # logged-in ($3=NULL)
            {"d": uuid.uuid4(), "uid": None, "gid": "guest123"},    # guest
            {"d": uuid.uuid4(), "uid": None, "gid": None},          # legacy
        ):
            # Old SQL raised AmbiguousParameter here; CASTs now type each NULL.
            db.execute(text(wrapped), params)
    finally:
        db.close()


def _make_account(db, account_id: uuid.UUID | None = None) -> uuid.UUID:
    """Insert a minimal qb.account row (FK target for notes / account_entity)."""
    account_id = account_id or uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO qb.account (id, username) "
            "VALUES (:id, :username)"
        ),
        {"id": account_id, "username": f"u_{account_id.hex[:10]}"},
    )
    return account_id


def test_list_notes_returns_only_callers_own_rows():
    """Logged-in user must see only their own note; the previous query never
    returned anything because it 503'd before fetching."""
    db = SessionLocal()
    doc_id = uuid.uuid4()
    me = uuid.uuid4()
    other = uuid.uuid4()
    try:
        doc_id = _make_document(db)
        me = _make_account(db, me)
        other = _make_account(db, other)
        db.commit()
        saved_notes_service.add_note(db, doc_id, content="mine", account_id=me)
        saved_notes_service.add_note(db, doc_id, content="theirs", account_id=other)
        mine = saved_notes_service.list_notes(db, doc_id, account_id=me, guest_id=None)
        assert [n["content"] for n in mine] == ["mine"]
    finally:
        db.rollback()
        db.execute(
            text("DELETE FROM qb.document_saved_notes WHERE document_id = :d"),
            {"d": doc_id},
        )
        db.execute(text("DELETE FROM qb.account WHERE id IN (:a, :b)"), {"a": me, "b": other})
        db.execute(
            text("DELETE FROM qb.documents WHERE id = :d"), {"d": doc_id}
        )
        db.commit()
        db.close()


def test_list_ideas_runs_for_guest_without_raising():
    db = SessionLocal()
    doc_id = uuid.uuid4()
    try:
        doc_id = _make_document(db)
        db.commit()
        # guest_id set, account_id None — the other NULL-binding branch.
        ideas = brainstorm_service.list_ideas(db, doc_id, account_id=None, guest_id="g1")
        assert ideas == []
    finally:
        db.rollback()
        db.execute(
            text("DELETE FROM qb.documents WHERE id = :d"), {"d": doc_id}
        )
        db.commit()
        db.close()


# ---------------------------------------------------------------------------
# Fix 2: _claim_guest_measurements() — immutable measurement table
# ---------------------------------------------------------------------------


def _seed_guest_measurement(db) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create a real guest account + entity + one graded answer measurement."""
    from app.models import Account

    guest_id = f"guest_{uuid.uuid4().hex[:8]}"
    guest_user = Account(id=uuid.uuid4(), username=guest_id)
    db.execute(
        text(
            "INSERT INTO qb.account (id, username) "
            "VALUES (:id, :username)"
        ),
        {"id": guest_user.id, "username": guest_id},
    )
    guest_entity = resolve_subject_entity(db, guest_user, None)
    artifact_id = uuid.uuid4()
    assertion_id = _make_assertion(db, artifact_id=artifact_id)
    record_answer_signal(
        db,
        subject_entity_id=guest_entity,
        assertion_id=assertion_id,
        correct=True,
        choice_index=0,
    )
    db.commit()
    return guest_user.id, guest_entity, assertion_id


def _account_entity(db, account_id: uuid.UUID, username: str) -> uuid.UUID:
    from app.models import Account

    db.execute(
        text(
            "INSERT INTO qb.account (id, username) "
            "VALUES (:id, :username)"
        ),
        {"id": account_id, "username": username},
    )
    return resolve_subject_entity(db, Account(id=account_id, username=username), None)


def _count_measurements(db, entity_id: uuid.UUID) -> int:
    return int(
        db.execute(
            text(
                "SELECT count(*) FROM intel.measurement WHERE subject_entity_id = :e"
            ),
            {"e": entity_id},
        ).scalar()
        or 0
    )


def test_claim_guest_measurements_copies_rows_without_violating_immutability():
    """Previously raised ``intel.measurement is immutable (INSERT only)``.

    intel.measurement is INSERT-only (the trigger blocks DELETE too), so — like
    test_progress_api — we never commit and roll back everything we created."""
    db = SessionLocal()
    try:
        _guest_user_id, guest_entity, assertion_id = _seed_guest_measurement(db)
        assert _count_measurements(db, guest_entity) == 1

        account_id = uuid.uuid4()
        account_entity = _account_entity(db, account_id, f"user_{account_id.hex[:8]}")

        moved = _claim_guest_measurements(
            db, guest_entity_id=guest_entity, account_entity_id=account_entity
        )

        assert moved == 1
        # New row attributed to the account...
        assert _count_measurements(db, account_entity) == 1
        # ...and the immutable guest row is left in place (INSERT-only table).
        assert _count_measurements(db, guest_entity) == 1
    finally:
        db.rollback()
        db.close()


def test_claim_guest_measurements_is_idempotent():
    """A repeated signup (or replay) must not duplicate rows on the account."""
    db = SessionLocal()
    try:
        _guest_user_id, guest_entity, assertion_id = _seed_guest_measurement(db)
        account_id = uuid.uuid4()
        account_entity = _account_entity(db, account_id, f"user_{account_id.hex[:8]}")

        first = _claim_guest_measurements(
            db, guest_entity_id=guest_entity, account_entity_id=account_entity
        )
        second = _claim_guest_measurements(
            db, guest_entity_id=guest_entity, account_entity_id=account_entity
        )

        assert first == 1
        assert second == 0  # already attributed — ON CONFLICT DO NOTHING
        assert _count_measurements(db, account_entity) == 1
    finally:
        db.rollback()
        db.close()


def test_claim_guest_progress_end_to_end_does_not_503():
    """Full signup path: claim_guest_progress must complete cleanly for a guest
    who practiced before signing up (the prod trigger). Rolled back because the
    measurement table is INSERT-only."""
    db = SessionLocal()
    new_account_id = uuid.uuid4()
    try:
        guest_user_id, _guest_entity, assertion_id = _seed_guest_measurement(db)
        # The new account must exist before claim_guest_progress links an
        # intel.entity to it (get_or_create_account_entity FK).
        _make_account(db, new_account_id)
        # Treat the seeded guest account as the guest identity for the claim.
        result = claim_guest_progress(
            db,
            account_id=new_account_id,
            username=f"newuser_{new_account_id.hex[:8]}",
            guest_id=str(guest_user_id),
        )
        # No exception means the immutable trigger was not hit.
        assert "measurements_moved" in result
    finally:
        db.rollback()
        db.close()
