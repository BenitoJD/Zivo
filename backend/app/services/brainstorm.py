"""Brainstorm mode — the ideas a learner kept from a brainstorming conversation.

Brainstorm has no generated artifact and no worker: the conversation itself runs on
the shared chat surface (see ``study_api.chat``). The only thing persisted is what the
learner explicitly saves, on qb.document_brainstorm_ideas.

``parent_id`` is the whole design. The idea board and the mind map are not two
features over two stores — they are one tree read two ways: ``list_ideas`` returns
the flat rows (board, newest first), ``build_tree`` nests the same rows (map).

Owner scoping (``account_id``) closes an IDOR on shared documents: when
``require_document`` grants read access to every user (``is_demo`` /
``is_public`` practice sources), only the idea's author may delete or list their
own ideas. ``account_id IS NULL`` rows are legacy / guest ideas and stay visible
to all callers of the document. Raw parameterized SQL, matching
``app.services.saved_notes``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.services.owner_scope import note_owner_scope, owner_scope_sql
from app.services.session_design import plan_brainstorm_tree_depth, plan_learner_list_cap


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "parent_id": pick(bool(r["parent_id"]), lambda: str(r["parent_id"]), lambda: None),
        "text": r["text"],
        "angle": r["angle"] or "",
        "created_at": pick(bool(r["created_at"]), lambda: r["created_at"].isoformat(), lambda: None),
    }


def list_ideas(
    db: Session,
    document_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None = None,
) -> list[dict[str, Any]]:
    uid, gid = note_owner_scope(account_id, guest_id)
    rows = db.execute(
        text(
            f"SELECT id, parent_id, text, angle, created_at FROM qb.document_brainstorm_ideas "
            f"WHERE {owner_scope_sql()} "
            "ORDER BY created_at DESC LIMIT :lim"
        ),
        {"d": document_id, "uid": uid, "gid": gid, "lim": plan_learner_list_cap("brainstorm")},
    ).mappings().all()
    return [_row(r) for r in rows]


def add_idea(
    db: Session,
    document_id: uuid.UUID,
    *,
    idea_text: str,
    angle: str = "",
    parent_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None,
    guest_id: str | None = None,
) -> dict[str, Any]:
    uid, gid = note_owner_scope(account_id, guest_id)

    def _check_parent() -> None:
        parent = db.execute(
            text(
                f"SELECT document_id FROM qb.document_brainstorm_ideas "
                f"WHERE id = :p AND {owner_scope_sql()}"
            ),
            {"p": parent_id, "d": document_id, "uid": uid, "gid": gid},
        ).scalar()

        def _bad() -> None:
            raise ValueError("parent idea does not belong to this document")

        pick(parent != document_id, _bad, lambda: None)

    pick(parent_id is not None, _check_parent, lambda: None)
    row = db.execute(
        text(
            """
            INSERT INTO qb.document_brainstorm_ideas (id, document_id, account_id, guest_id, parent_id, text, angle)
            VALUES (:id, :d, :uid, :gid, :parent, :text, :angle)
            RETURNING id, parent_id, text, angle, created_at
            """
        ),
        {
            "id": uuid.uuid4(),
            "d": document_id,
            "uid": uid,
            "gid": gid,
            "parent": parent_id,
            "text": idea_text,
            "angle": angle,
        },
    ).mappings().first()
    db.commit()
    return _row(row)


def delete_idea(
    db: Session,
    document_id: uuid.UUID,
    idea_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None = None,
) -> bool:
    uid, gid = note_owner_scope(account_id, guest_id)
    res = db.execute(
        text(
            f"DELETE FROM qb.document_brainstorm_ideas "
            f"WHERE id = :id AND {owner_scope_sql()}"
        ),
        {"id": idea_id, "d": document_id, "uid": uid, "gid": gid},
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
        parent = pick(bool(idea["parent_id"]), lambda: by_id.get(idea["parent_id"]), lambda: None)
        pick(
            parent is None or parent is idea,
            lambda: roots.append(idea),
            lambda: parent["children"].append(idea),
        )
    return roots


def to_markdown(title: str, ideas: list[dict[str, Any]]) -> str:
    """Render the idea tree as nested markdown bullets — the export payload.

    One text format, because every export target the UI offers (`.md` download,
    clipboard, print-to-PDF) renders from it.
    """
    lines = [f"# Brainstorm — {title}".rstrip(" —"), ""]

    def _empty() -> str:
        lines.append("_No ideas kept yet._")
        return "\n".join(lines) + "\n"

    def _filled() -> str:
        def walk(nodes: list[dict[str, Any]], depth: int) -> None:
            pick(
                depth > plan_brainstorm_tree_depth(),
                lambda: None,
                lambda: _walk_nodes(nodes, depth),
            )

        def _walk_nodes(nodes: list[dict[str, Any]], depth: int) -> None:
            for node in nodes:
                angle = pick(bool(node["angle"]), lambda: f" _({node['angle']})_", lambda: "")
                body = " ".join((node["text"] or "").split())
                lines.append(f"{'  ' * depth}- {body}{angle}")
                walk(node["children"], depth + 1)

        walk(build_tree(ideas), 0)
        return "\n".join(lines) + "\n"

    return pick(not ideas, _empty, _filled)
