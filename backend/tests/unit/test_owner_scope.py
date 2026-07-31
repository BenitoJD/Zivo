"""Regression guard for the owner-scope SQL used by saved notes + brainstorm ideas.

``owner_scope_sql()`` is shared by ``app.services.saved_notes`` and
``app.services.brainstorm``. It always binds both ``:uid`` and ``:gid``, and
``note_owner_scope`` always returns exactly one of them as ``None``. With
psycopg3 that NULL is sent untyped, and the old ``(:uid IS NOT NULL AND ...)``
form gave Postgres no way to infer the parameter type — every document open
failed with ``could not determine data type of parameter $3`` and surfaced as
a 503 "Database unavailable" (see prod logs, 2026-07-31).

These unit tests assert the shape that prevents that: an explicit CAST on each
NULLable param, NULL-safe equality (``IS NOT DISTINCT FROM``), and unchanged
param names so callers don't need edits. The SQL itself is exercised against
the live schema in ``tests/integration/test_owner_scope.py``.
"""

from __future__ import annotations

import re

from app.services.owner_scope import note_owner_scope, owner_scope_sql

_SQL = owner_scope_sql()


def test_uid_param_is_cast_so_untyped_null_is_typed():
    # Without CAST(:uid AS uuid), psycopg3 sends None as an untyped NULL and
    # Postgres rejects "could not determine data type of parameter $N".
    assert re.search(r"CAST\(\s*:uid\s+AS\s+uuid\s*\)", _SQL), _SQL


def test_gid_param_is_cast_so_untyped_null_is_typed():
    assert re.search(r"CAST\(\s*:gid\s+AS\s+text\s*\)", _SQL), _SQL


def test_equality_is_null_safe():
    # account_id is NOT NULL for user rows but NULL for guest/legacy rows, so
    # the comparison must be NULL-safe (= NULL returns NULL, not true).
    assert "IS NOT DISTINCT FROM" in _SQL, _SQL


def test_param_names_unchanged_so_callers_need_no_edits():
    # saved_notes.py / brainstorm.py build the params dict {d, uid, gid, ...};
    # renaming a placeholder here would break them silently.
    for name in (":d", ":uid", ":gid"):
        assert name in _SQL, name


def test_note_owner_scope_returns_exactly_one_side_set():
    import uuid

    uid = uuid.uuid4()
    # Logged-in user -> uid set, gid None (the case that hit "$3 ambiguous").
    assert note_owner_scope(uid, None) == (uid, None)
    assert note_owner_scope(uid, "g1") == (uid, None)  # user wins
    # Guest only -> uid None, gid set.
    assert note_owner_scope(None, "g1") == (None, "g1")
    # Neither -> both None (legacy rows visible to all callers of the document).
    assert note_owner_scope(None, None) == (None, None)
