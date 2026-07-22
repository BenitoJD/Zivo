"""Brainstorm idea tree — nesting + markdown export (the pure, DB-free half)."""

from __future__ import annotations

from app.services.brainstorm import build_tree, to_markdown


def _idea(idea_id: str, parent: str | None, text: str, at: str, angle: str = "") -> dict:
    return {
        "id": idea_id,
        "parent_id": parent,
        "text": text,
        "angle": angle,
        "created_at": at,
    }


def test_build_tree_nests_children_oldest_first():
    ideas = [
        _idea("c", "a", "child two", "2026-07-22T00:03:00"),
        _idea("b", "a", "child one", "2026-07-22T00:02:00"),
        _idea("a", None, "root", "2026-07-22T00:01:00"),
    ]
    tree = build_tree(ideas)
    assert [n["id"] for n in tree] == ["a"]
    # Within a branch, ideas read in the order they were thought of.
    assert [n["text"] for n in tree[0]["children"]] == ["child one", "child two"]


def test_orphaned_idea_is_promoted_not_dropped():
    """A child whose parent is missing (paged out, or deleted mid-read) must still
    appear on the board — silently losing kept ideas is the one unacceptable bug."""
    ideas = [_idea("x", "gone", "orphan", "2026-07-22T00:01:00")]
    tree = build_tree(ideas)
    assert [n["id"] for n in tree] == ["x"]


def test_to_markdown_indents_by_depth_and_labels_angle():
    ideas = [
        _idea("a", None, "root idea", "2026-07-22T00:01:00", angle="tension"),
        _idea("b", "a", "branch idea", "2026-07-22T00:02:00"),
    ]
    md = to_markdown("Thermodynamics", ideas)
    assert "# Brainstorm — Thermodynamics" in md
    assert "- root idea _(tension)_" in md
    assert "  - branch idea" in md


def test_to_markdown_handles_empty():
    assert "_No ideas kept yet._" in to_markdown("Anything", [])


def test_build_tree_survives_a_parent_cycle():
    """A row pointing at itself must not recurse forever."""
    ideas = [_idea("a", "a", "self parent", "2026-07-22T00:01:00")]
    assert [n["id"] for n in build_tree(ideas)] == ["a"]
