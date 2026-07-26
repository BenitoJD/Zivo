"""Unit tests for the Learn lesson pipeline (parser, formatter, store SQL).

Covers the pure pieces of the per-page lesson feature — no DB and no LLM call:
  * ``_parse_lesson`` — tolerant ``zv-lesson`` JSON extraction + truncation caps
  * ``_aspects_block`` — triage aspects -> the prompt's teaching spine
  * ``ArtifactStore(extra_cols=...)`` — the ``content_hash`` column rides along
    on both the save and set_status upserts (the one new wrinkle vs. notes/topics).

The DB-backed round-trip (save -> load -> get_lesson) is exercised against the
live dev database in the smoke test, not here — these tests stay hermetic.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.graphs.lesson_graph import LESSON_POLICY_VERSION, _aspects_block, _parse_lesson
from app.services.artifact_store import ArtifactStore
from app.services.page_lessons import _LESSON_STORE, _row_to_state


# ----------------------------------------------------------------------- parser


def test_parse_lesson_handles_zv_lesson_fence() -> None:
    raw = (
        '```zv-lesson\n'
        '{"title":"How catalysts work","body":"A catalyst lowers the energy hill.\\n\\nIt is **not** used up."}\n'
        '```'
    )
    parsed = _parse_lesson(raw)
    assert parsed == {
        "title": "How catalysts work",
        "body": "A catalyst lowers the energy hill.\n\nIt is **not** used up.",
    }


def test_parse_lesson_handles_preamble_before_fence() -> None:
    # Models sometimes prepend a sentence before the block — extract_json_obj's
    # fallback (first { to last }) must still salvage the JSON.
    raw = (
        'Here is your lesson.\n\n'
        '```zv-lesson\n{"title":"T","body":"Body text."}\n```'
    )
    assert _parse_lesson(raw) == {"title": "T", "body": "Body text."}


def test_parse_lesson_bare_json_no_fence() -> None:
    raw = '{"title":"Bare","body":"No fence at all."}'
    assert _parse_lesson(raw) == {"title": "Bare", "body": "No fence at all."}


def test_parse_lesson_returns_none_on_empty_or_missing_body() -> None:
    assert _parse_lesson("") is None
    assert _parse_lesson("   ") is None
    assert _parse_lesson('{"title":"No body"}') is None
    assert _parse_lesson('{"title":"T","body":""}') is None
    assert _parse_lesson("not json at all") is None


def test_parse_lesson_falls_back_to_first_sentence_when_title_missing() -> None:
    raw = '{"body":"First sentence here. Second sentence."}'
    parsed = _parse_lesson(raw)
    assert parsed is not None
    # The title fallback takes the first sentence (split on ". "), without the
    # trailing period — the body is preserved verbatim.
    assert parsed["title"] == "First sentence here"
    assert parsed["body"] == "First sentence here. Second sentence."


def test_parse_lesson_truncates_overlong_fields() -> None:
    long_body = "x" * 5000
    long_title = "t" * 500
    parsed = _parse_lesson(f'{{"title":"{long_title}","body":"{long_body}"}}')
    assert parsed is not None
    assert len(parsed["body"]) <= 2400
    assert len(parsed["title"]) <= 160


# ------------------------------------------------------------------ aspects fmt


def test_aspects_block_renders_central_aspects_with_angle() -> None:
    aspects = [
        {"key": "catalysis", "label": "Catalysis mechanism", "centrality": "central", "cognitive_angle": "mechanism"},
        {"key": "energy", "label": "Activation energy", "centrality": "central", "cognitive_angle": "recall"},
    ]
    block = _aspects_block(aspects)
    assert "- Catalysis mechanism (mechanism)" in block
    # recall angle is the default — suppressed to reduce prompt noise
    assert "- Activation energy" in block
    assert "recall" not in block


def test_aspects_block_skips_peripheral_aspects() -> None:
    aspects = [
        {"key": "main", "label": "Main idea", "centrality": "central"},
        {"key": "side", "label": "Side detail", "centrality": "support"},
    ]
    block = _aspects_block(aspects)
    assert "Main idea" in block
    assert "Side detail" not in block


def test_aspects_block_empty_falls_back_to_default() -> None:
    assert _aspects_block([]) == "- the main idea of this page"
    assert _aspects_block(None) == "- the main idea of this page"


# ------------------------------------------------------- store SQL construction


def test_lesson_store_save_includes_content_hash_extra_col() -> None:
    db = MagicMock()
    doc_id = uuid.uuid4()

    _LESSON_STORE.save(
        db,
        doc_id,
        {"title": "T", "body": "B", "policy_version": LESSON_POLICY_VERSION},
        page_number=3,
        content_hash="abc123",
    )

    sql = str(db.execute.call_args.args[0])
    params = db.execute.call_args.args[1]
    # content_hash is an extra_col — it must appear in the column list and the
    # ON CONFLICT update set, alongside the natural key (document_id, page_number).
    assert "content_hash" in sql
    assert "page_number" in sql
    assert "ON CONFLICT (document_id, page_number)" in sql
    assert params["page_number"] == 3
    assert params["content_hash"] == "abc123"
    assert params["id"] == doc_id


def test_lesson_store_set_status_includes_content_hash() -> None:
    db = MagicMock()
    doc_id = uuid.uuid4()

    _LESSON_STORE.set_status(db, doc_id, "generating", page_number=1, content_hash="h1")

    sql = str(db.execute.call_args.args[0])
    params = db.execute.call_args.args[1]
    assert "content_hash" in sql
    assert params["content_hash"] == "h1"
    assert params["status"] == "generating"


def test_lesson_store_conflict_target_is_composite_key() -> None:
    # The store's ON CONFLICT target must match the table PK exactly.
    assert _LESSON_STORE.conflict_target == "document_id, page_number"


# -------------------------------------------------------------- row -> payload


def test_row_to_state_ready_with_body() -> None:
    row = {
        "status": "ready",
        "lesson": {"title": "T", "body": "B", "policy_version": LESSON_POLICY_VERSION},
        "error": None,
        "content_hash": "h",
    }
    state = _row_to_state(row)
    assert state["status"] == "ready"
    assert state["title"] == "T"
    assert state["body"] == "B"


def test_row_to_state_missing_row_returns_missing_status() -> None:
    state = _row_to_state(None)
    assert state["status"] == "missing"
    assert state["title"] is None
    assert state["body"] is None


def test_row_to_state_generating_returns_none_body() -> None:
    row = {"status": "generating", "lesson": {}, "error": None, "content_hash": ""}
    state = _row_to_state(row)
    assert state["status"] == "generating"
    assert state["body"] is None
