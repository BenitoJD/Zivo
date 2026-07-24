"""Unit tests for newspaper Telethon channel-resolution helpers."""

from run_newspaper_ingest import (
    _dialog_id_matches,
    _dialog_title_matches,
    _entity_ref,
)


def test_entity_ref_coerces_marked_peer_id() -> None:
    assert _entity_ref("-1003384637541") == -1003384637541
    assert _entity_ref(" @MyChannel ") == "@MyChannel"
    assert _entity_ref("") == ""


def test_dialog_id_matches_marked_and_bare() -> None:
    marked = -1003384637541
    bare = 3384637541
    assert _dialog_id_matches(marked, bare, marked)
    assert _dialog_id_matches(marked, bare, bare)
    assert not _dialog_id_matches(marked, bare, -100999)


def test_dialog_title_matches_label_and_fallback() -> None:
    label = "MyBookZon ENGLISH (PREMIUM)"
    assert _dialog_title_matches(label, label=label, ref="") == "title_exact_label"
    assert (
        _dialog_title_matches(
            "MyBookZon ENGLISH (PREMIUM) — today",
            label="",
            ref="",
        )
        == "title_contains_mybookzon_english"
    )
    assert _dialog_title_matches("Other Channel", label=label, ref="") is None
