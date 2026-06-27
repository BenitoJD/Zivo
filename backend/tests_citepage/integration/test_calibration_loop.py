"""Calibration write-path round-trip — the DB glue behind the tutor loop.

These exercise the real Postgres path that unit tests can't reach: that an answer
outcome lands as projection rows and that difficulty reads back keyed correctly
(the subject_assertion_id::text = ANY(text[]) cast in particular). Every test runs
inside one transaction and rolls back, so it leaves no rows behind.

Skipped automatically when the dev DB is unreachable.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.repositories.intel import concept_id, get_or_create_concept_entity
from app.services.answer_signal import record_answer_signal
from app.services.calibration import (
    ABILITY_PROJECTION_URI,
    DEFAULT_RATING,
    DIFFICULTY_PROJECTION_URI,
    _latest_rating,
    record_outcome,
)
from app.services.question_pool import _difficulty_for_ids, _lineage_successors


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def _make_assertion(db: Session, *, page: int, sequence: int, concept_key: str) -> uuid.UUID:
    """Insert a minimal active MCQ assertion, mirroring generation_graph.py."""
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
            "uri": f"qb://assertion/test/{assertion_id}",
            "fp": f"test:{assertion_id}",
            "title": "Test question?",
            "summary": "Test explanation.",
            "payload": json.dumps(
                {"page_number": page, "sequence": sequence, "primary_concept_key": concept_key}
            ),
        },
    )
    return assertion_id


def _learner(db: Session) -> uuid.UUID:
    return get_or_create_concept_entity(db, f"guest:test:{uuid.uuid4().hex}", "Test learner")


def test_record_outcome_appends_ability_and_difficulty_that_move() -> None:
    db = SessionLocal()
    try:
        learner = _learner(db)
        item = _make_assertion(db, page=1, sequence=0, concept_key="k")

        first = record_outcome(db, subject_entity_id=learner, assertion_id=item, correct=True)
        # A correct answer lifts the learner above and eases the item below origin.
        assert first.ability > DEFAULT_RATING > first.difficulty

        ability, n = _latest_rating(db, ABILITY_PROJECTION_URI, entity_id=learner)
        assert n == 1
        assert ability == pytest.approx(first.ability)

        difficulty, dn = _latest_rating(db, DIFFICULTY_PROJECTION_URI, assertion_id=item)
        assert dn == 1
        assert difficulty == pytest.approx(first.difficulty)

        # Another correct answer keeps the learner climbing; history accumulates.
        second = record_outcome(db, subject_entity_id=learner, assertion_id=item, correct=True)
        assert second.ability > first.ability
        _, n2 = _latest_rating(db, ABILITY_PROJECTION_URI, entity_id=learner)
        assert n2 == 2
    finally:
        db.rollback()
        db.close()


def test_difficulty_for_ids_reads_latest_per_item_by_text_id() -> None:
    db = SessionLocal()
    try:
        learner = _learner(db)
        easy = _make_assertion(db, page=1, sequence=0, concept_key="a")
        hard = _make_assertion(db, page=1, sequence=1, concept_key="b")

        # Nail the easy item (difficulty drops); miss the hard item (difficulty rises).
        record_outcome(db, subject_entity_id=learner, assertion_id=easy, correct=True)
        record_outcome(db, subject_entity_id=learner, assertion_id=hard, correct=False)

        diffs = _difficulty_for_ids(db, [str(easy), str(hard)])
        assert set(diffs) == {str(easy), str(hard)}
        assert diffs[str(hard)] > diffs[str(easy)]

        # An uncalibrated id is simply absent (selection treats it as neutral).
        assert _difficulty_for_ids(db, [str(uuid.uuid4())]) == {}
    finally:
        db.rollback()
        db.close()


def _link(db: Session, from_id: uuid.UUID, to_id: uuid.UUID, link_uri: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO intel.assertion_lineage
              (from_assertion_id, to_assertion_id, link_type_concept_id)
            VALUES (:f, :t, :lt)
            """
        ),
        {"f": from_id, "t": to_id, "lt": concept_id(db, link_uri)},
    )


def test_answer_signal_is_idempotent_and_calibrates_once() -> None:
    db = SessionLocal()
    try:
        learner = _learner(db)
        item = _make_assertion(db, page=1, sequence=0, concept_key="k")

        first = record_answer_signal(
            db, subject_entity_id=learner, assertion_id=item,
            correct=True, choice_index=0, calibrate=True,
        )
        assert first.inserted is True
        assert first.ability is not None  # calibrated on the new event

        # Replay / double-grade the same answer → no-op, no calibration.
        second = record_answer_signal(
            db, subject_entity_id=learner, assertion_id=item,
            correct=True, choice_index=0, calibrate=True,
        )
        assert second.inserted is False
        assert second.ability is None

        # Exactly one measurement row and one ability projection — never double-counted.
        n_rows = db.execute(
            text(
                "SELECT count(*) FROM intel.measurement "
                "WHERE subject_entity_id = :e AND source_assertion_id = :a"
            ),
            {"e": learner, "a": item},
        ).scalar()
        assert n_rows == 1
        n_ability = db.execute(
            text(
                "SELECT count(*) FROM intel.projection "
                "WHERE subject_entity_id = :e AND type_concept_id = :t"
            ),
            {"e": learner, "t": concept_id(db, ABILITY_PROJECTION_URI)},
        ).scalar()
        assert n_ability == 1
    finally:
        db.rollback()
        db.close()


def test_lineage_successors_reads_followup_and_harder_edges() -> None:
    db = SessionLocal()
    try:
        asked = _make_assertion(db, page=1, sequence=0, concept_key="k")
        remedial = _make_assertion(db, page=1, sequence=1, concept_key="k")
        harder = _make_assertion(db, page=1, sequence=2, concept_key="k")
        unrelated = _make_assertion(db, page=1, sequence=3, concept_key="k")
        _link(db, asked, remedial, "/vocab/link/follow_up_after_miss")
        _link(db, asked, harder, "/vocab/link/harder_than")

        out = _lineage_successors(
            db, str(asked), [str(remedial), str(harder), str(unrelated)]
        )
        assert out == {
            str(remedial): "follow_up_after_miss",
            str(harder): "harder_than",
        }
    finally:
        db.rollback()
        db.close()
