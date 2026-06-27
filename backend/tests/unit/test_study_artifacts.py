"""Unit tests for the Scribely-style study-artifact parsers (notes + flashcards).

Pure functions only — no DB, no network. Locks the tolerant parsing that the
intermittent-provider reality demands (truncated arrays, fenced JSON, alt keys).
"""

from app.graphs.flashcards_graph import _MAX_CARDS, _finalize, _loads_obj, _parse_cards
from app.graphs.notes_graph import _clean


def test_parse_cards_plain_array():
    raw = '[{"front": "Q1?", "back": "A1", "kind": "qa"}, {"front": "fill ___", "back": "gap", "kind": "cloze"}]'
    cards = _parse_cards(raw)
    assert [c["kind"] for c in cards] == ["qa", "cloze"]
    assert cards[0]["front"] == "Q1?" and cards[0]["back"] == "A1"


def test_parse_cards_strips_json_fence():
    raw = '```json\n[{"front": "Q", "back": "A"}]\n```'
    cards = _parse_cards(raw)
    assert len(cards) == 1 and cards[0]["kind"] == "qa"  # kind defaults to qa


def test_parse_cards_accepts_question_answer_aliases():
    raw = '[{"question": "What?", "answer": "This"}]'
    cards = _parse_cards(raw)
    assert cards == [{"front": "What?", "back": "This", "kind": "qa"}]


def test_parse_cards_salvages_truncated_array():
    # Provider cut the stream mid-array — the last object is incomplete.
    raw = (
        '[\n {"front": "Q1", "back": "A1", "kind": "qa"},\n'
        '   {"front": "Q2", "back": "A2", "kind": "cloze"},\n'
        '   {"front": "Q3", "back": '
    )
    cards = _parse_cards(raw)
    assert [c["front"] for c in cards] == ["Q1", "Q2"]


def test_parse_cards_drops_incomplete_and_normalizes_kind():
    raw = '[{"front": "Q", "back": ""}, {"front": "Q2", "back": "A2", "kind": "weird"}]'
    cards = _parse_cards(raw)
    assert cards == [{"front": "Q2", "back": "A2", "kind": "qa"}]


def test_parse_cards_empty_and_garbage():
    assert _parse_cards("") == []
    assert _parse_cards("not json at all") == []


def test_finalize_dedupes_and_caps():
    cards = [{"front": f"Q{i}", "back": "A", "kind": "qa"} for i in range(_MAX_CARDS + 10)]
    cards += [{"front": "q0", "back": "dup", "kind": "qa"}]  # case-insensitive dup of Q0
    out = _finalize(cards)
    assert len(out) == _MAX_CARDS
    assert len({c["front"].lower() for c in out}) == _MAX_CARDS


def test_loads_obj():
    assert _loads_obj('{"a": 1}') == {"a": 1}
    assert _loads_obj("[1,2]") is None
    assert _loads_obj("{broken") is None


def test_notes_clean_strips_outer_markdown_fence():
    assert _clean("```markdown\n# Title\n- point\n```") == "# Title\n- point"
    assert _clean("```\n# Title\n```") == "# Title"
    assert _clean("# Already clean\n- x") == "# Already clean\n- x"
