"""Brainstorm mode — the ideas a learner kept from a brainstorming conversation.

Brainstorm has no generated artifact and no worker: the conversation itself runs on
the shared chat surface (see ``app.api.chat``). The only thing persisted is what the
learner explicitly saves, on qb.document_brainstorm_ideas.

``parent_id`` is the whole design. The idea board and the mind map are not two
features over two stores — they are one tree read two ways: ``list_ideas`` returns
the flat rows (board, newest first), ``build_tree`` nests the same rows (map).
Raw parameterized SQL, matching ``app.services.saved_notes``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

_MAX_IDEAS = 500
# ponytail: depth cap exists so a cycle or a runaway branch can't hang the renderer.
# Raise it if real mind maps ever get deeper than this; nothing else depends on it.
_MAX_DEPTH = 12


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
        "text": r["text"],
        "angle": r["angle"] or "",
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


def list_ideas(db: Session, document_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every kept idea for this source, newest first (the board ordering)."""
    rows = db.execute(
        text(
            "SELECT id, parent_id, text, angle, created_at FROM qb.document_brainstorm_ideas "
            "WHERE document_id = :d ORDER BY created_at DESC LIMIT :lim"
        ),
        {"d": document_id, "lim": _MAX_IDEAS},
    ).mappings().all()
    return [_row(r) for r in rows]


def add_idea(
    db: Session,
    document_id: uuid.UUID,
    *,
    idea_text: str,
    angle: str = "",
    parent_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Keep one idea. ``parent_id`` branches it off an existing idea (mind-map edge).

    A parent from another document is rejected rather than silently orphaned — the
    FK alone would accept it, since it only constrains the ideas table.
    """
    if parent_id is not None:
        owner = db.execute(
            text("SELECT document_id FROM qb.document_brainstorm_ideas WHERE id = :p"),
            {"p": parent_id},
        ).scalar()
        if owner != document_id:
            raise ValueError("parent idea does not belong to this document")
    row = db.execute(
        text(
            """
            INSERT INTO qb.document_brainstorm_ideas (id, document_id, parent_id, text, angle)
            VALUES (:id, :d, :parent, :text, :angle)
            RETURNING id, parent_id, text, angle, created_at
            """
        ),
        {
            "id": uuid.uuid4(),
            "d": document_id,
            "parent": parent_id,
            "text": idea_text,
            "angle": angle,
        },
    ).mappings().first()
    db.commit()
    return _row(row)


def delete_idea(db: Session, document_id: uuid.UUID, idea_id: uuid.UUID) -> bool:
    """Delete an idea and (via ON DELETE CASCADE) everything branched off it."""
    res = db.execute(
        text("DELETE FROM qb.document_brainstorm_ideas WHERE id = :id AND document_id = :d"),
        {"id": idea_id, "d": document_id},
    )
    db.commit()
    return res.rowcount > 0


def build_tree(ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nest flat idea rows into roots -> children (the mind-map shape).

    Oldest first within each level so a branch reads in the order it was thought of.
    Any idea whose parent is missing is promoted to a root, so a partial read can
    never drop ideas off the map.
    """
    by_id = {i["id"]: {**i, "children": []} for i in ideas}
    roots: list[dict[str, Any]] = []
    for idea in sorted(by_id.values(), key=lambda i: i["created_at"] or ""):
        parent = by_id.get(idea["parent_id"]) if idea["parent_id"] else None
        if parent is None or parent is idea:
            roots.append(idea)
        else:
            parent["children"].append(idea)
    return roots


def to_markdown(title: str, ideas: list[dict[str, Any]]) -> str:
    """Render the idea tree as nested markdown bullets — the export payload.

    One text format, because every export target the UI offers (`.md` download,
    clipboard, print-to-PDF) renders from it.
    """
    lines = [f"# Brainstorm — {title}".rstrip(" —"), ""]
    if not ideas:
        lines.append("_No ideas kept yet._")
        return "\n".join(lines) + "\n"

    def walk(nodes: list[dict[str, Any]], depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        for node in nodes:
            angle = f" _({node['angle']})_" if node["angle"] else ""
            body = " ".join((node["text"] or "").split())
            lines.append(f"{'  ' * depth}- {body}{angle}")
            walk(node["children"], depth + 1)

    walk(build_tree(ideas), 0)
    return "\n".join(lines) + "\n"
