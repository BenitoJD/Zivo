"""Shared storage + orchestration for cached per-document "study artifacts".

Notes, flashcards, quizzes, memory palaces, resumes, topic outlines … every longform
study artifact follows the same lifecycle in ``qb.document_*``:

  * ``status`` column cycles ``missing → generating → ready | failed``;
  * the read path (``ensure_*``) returns immediately, enqueuing background generation
    the first time the artifact is requested;
  * the worker path (``run_*_generation``) sets ``generating``, awaits the generator
    coroutine, and either persists the result (``ready``) or records the failure.

The classes here capture that skeleton so each artifact module only states what is
genuinely unique to it: the table, the key, the payload column, and the generator.
Modules whose ``ensure_*`` read path has non-trivial logic (memory palace's
setting-driven regenerate, resume's config-version check) keep their own ``ensure_*``
and only adopt ``ArtifactStore`` for the load/save/status/store primitives + the
``run_artifact_generation`` worker wrapper.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import choose, pick


def coerce_jsonb(value: Any) -> Any:
    """Re-parse a JSONB column that psycopg may return as a str instead of parsed JSON."""
    def _from_str() -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    return pick(isinstance(value, str), _from_str, lambda: value)


class ArtifactStore:
    """Parameterized load/save/status helpers for a single ``qb.document_*`` table.

    Encapsulates the verbatim INSERT … ON CONFLICT upsert pattern shared by every
    artifact table, so a new artifact type only declares its shape rather than
    re-writing the SQL.

    Parameters
    ----------
    table:
        e.g. ``"qb.document_flashcards"``.
    key_cols:
        The natural-key column(s) that identify a row, in order, *excluding* the
        always-present ``document_id`` (e.g. ``[]`` for one-per-document artifacts,
        ``["kind"]`` for notes, ``["topic_key"]`` for explanations). ``document_id``
        is always the first key column and is added automatically.
    payload_col:
        The column that holds the generated artifact, or ``None`` for status-only
        upserts. When set and ``cast_jsonb`` is true (the default), the value is
        serialized with ``json.dumps`` and inserted via ``CAST(:x AS jsonb)``.
    extra_cols:
        Extra non-key columns to write on every upsert (e.g. memory palace's
        ``setting``). Map of column name → param name; the caller passes the param.
    """

    _KEY_DOC_ID = "document_id"

    def __init__(
        self,
        *,
        table: str,
        key_cols: list[str] | None = None,
        payload_col: str | None,
        cast_jsonb: bool = True,
        extra_cols: dict[str, str] | None = None,
    ) -> None:
        self.table = table
        # document_id is always first; caller-supplied key_cols follow it.
        self.key_cols = [self._KEY_DOC_ID, *(key_cols or [])]
        self.payload_col = payload_col
        self.cast_jsonb = cast_jsonb and payload_col is not None
        self.extra_cols = extra_cols or {}

    @property
    def conflict_target(self) -> str:
        """ON CONFLICT target for the upsert — the natural key, scoped to document_id."""
        return ", ".join(self.key_cols)

    def _key_params(self, document_id: uuid.UUID, key_values: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {"id": document_id}
        for col in self.key_cols[1:]:
            pick(
                col not in key_values,
                lambda: (_raise_key(col), None)[1],
                lambda: params.__setitem__(col, key_values[col]),
            )
        return params

    def load_row(self, db: Session, document_id: uuid.UUID, **key_values: Any) -> dict[str, Any] | None:
        """Return the raw row as a mapping, or ``None`` if no row exists."""
        cols = choose(bool(self.payload_col), [self.payload_col], [])
        cols = cols + list(self.extra_cols.keys()) + ["status", "error"]
        select = ", ".join(cols)
        where = " AND ".join(
            f"{c} = :{choose(c != self._KEY_DOC_ID, c, 'id')}" for c in self.key_cols
        )
        row = db.execute(
            text(f"SELECT {select} FROM {self.table} WHERE {where}"),
            self._key_params(document_id, key_values),
        ).mappings().first()
        return pick(bool(row), lambda: dict(row), lambda: None)

    def set_status(
        self,
        db: Session,
        document_id: uuid.UUID,
        status: str,
        *,
        error: str | None = None,
        **key_and_extra: Any,
    ) -> None:
        """Upsert a status-only row (status + error + extra_cols), payload untouched."""
        extra_keys = list(self.extra_cols.keys())
        cols = list(self.key_cols) + extra_keys + ["status", "error", "updated_at"]
        col_list = ", ".join(cols)
        placeholders = [
            f":{choose(c != self._KEY_DOC_ID, c, 'id')}" for c in self.key_cols
        ]
        placeholders += [f":{self.extra_cols[c]}" for c in extra_keys]
        placeholders += [":status", ":error", "now()"]
        params: dict[str, Any] = {"id": document_id, "status": status, "error": error}
        params.update(self._key_params(document_id, key_and_extra))
        for col, pname in self.extra_cols.items():
            params[pname] = key_and_extra.get(col)
        update_cols = extra_keys + ["status", "error"]
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        db.execute(
            text(
                f"INSERT INTO {self.table} ({col_list}) VALUES ({', '.join(placeholders)}) "
                f"ON CONFLICT ({self.conflict_target}) DO UPDATE SET {update_set}, updated_at = now()"
            ),
            params,
        )

    def save(
        self,
        db: Session,
        document_id: uuid.UUID,
        payload: Any,
        **key_and_extra: Any,
    ) -> None:
        """Upsert a finished artifact (payload + status='ready' + NULL error)."""
        pick(self.payload_col is None, _raise_payload, lambda: None)

        extra_keys = list(self.extra_cols.keys())
        cols = list(self.key_cols) + extra_keys + [self.payload_col, "status", "error", "updated_at"]
        col_list = ", ".join(cols)

        payload_param = "payload"
        payload_sql = pick(
            self.cast_jsonb,
            lambda: f"CAST(:{payload_param} AS jsonb)",
            lambda: f":{payload_param}",
        )
        payload_value = pick(self.cast_jsonb, lambda: json.dumps(payload), lambda: payload)

        placeholders = [
            f":{choose(c != self._KEY_DOC_ID, c, 'id')}" for c in self.key_cols
        ]
        placeholders += [f":{self.extra_cols[c]}" for c in extra_keys]
        placeholders += [payload_sql, "'ready'", "NULL", "now()"]

        params: dict[str, Any] = {payload_param: payload_value, "id": document_id}
        params.update(self._key_params(document_id, key_and_extra))
        for col, pname in self.extra_cols.items():
            params[pname] = key_and_extra.get(col)

        update_cols = extra_keys + [self.payload_col, "status", "error"]
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        db.execute(
            text(
                f"INSERT INTO {self.table} ({col_list}) VALUES ({', '.join(placeholders)}) "
                f"ON CONFLICT ({self.conflict_target}) DO UPDATE SET {update_set}, updated_at = now()"
            ),
            params,
        )


def _raise_key(col: str) -> None:
    raise KeyError(f"key column {col!r} not provided")


def _raise_payload() -> None:
    raise ValueError("save() requires a payload_col")


def run_artifact_generation(
    db: Session,
    document_id: uuid.UUID,
    *,
    store: ArtifactStore,
    generate: Callable[[], Awaitable[Any]],
    is_complete: Callable[[Any], bool],
    empty_error: str,
    key_and_extra: dict[str, Any] | None = None,
) -> Any:
    """Worker skeleton: set generating → run coroutine → save or mark failed.

    Encapsulates the try/except/commit pattern repeated across every artifact worker.
    The caller supplies:

    * ``store`` — the ``ArtifactStore`` for persistence + status.
    * ``generate`` — a zero-arg factory returning the awaitable (so asyncio.run gets
      a fresh coroutine each call).
    * ``is_complete`` — predicate over the generator's result; False ⇒ ``failed`` with
      ``empty_error`` (e.g. empty string, zero questions, palace with no stations).
    * ``key_and_extra`` — passed through to ``store.set_status`` / ``store.save`` for
      multi-key artifacts (notes ``kind``) or extra columns (palace ``setting``).
    """
    kw = key_and_extra or {}
    store.set_status(db, document_id, "generating", **kw)
    db.commit()
    try:
        result = asyncio.run(generate())
    except Exception as exc:
        store.set_status(db, document_id, "failed", error=str(exc)[:500], **kw)
        db.commit()
        raise
    pick(
        is_complete(result),
        lambda: store.save(db, document_id, result, **kw),
        lambda: store.set_status(db, document_id, "failed", error=empty_error, **kw),
    )
    db.commit()
    return result
